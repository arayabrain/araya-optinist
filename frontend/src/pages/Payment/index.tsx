import { useState, useEffect } from "react"
import { useDispatch, useSelector } from "react-redux"
import { useSearchParams, useNavigate } from "react-router-dom"

import {
  Elements,
  CardElement,
  useStripe,
  useElements,
} from "@stripe/react-stripe-js"
import { loadStripe } from "@stripe/stripe-js"

import { getSubscriptionPlan } from "store/slice/Subscriptions/SubscriptionActions"
import { selectCurrentUser } from "store/slice/User/UserSelector"
import { AppDispatch } from "store/store"

// Load Stripe - Replace with your publishable key
const stripePromise = loadStripe(
  process.env.REACT_APP_STRIPE_PUBLISHABLE_KEY ||
    "pk_test_51RgYIgP7o6DukA7c9NOwUVyA2ObwzHmWX6y7xydeu7TkFicO0y8TWTGoGlI8b4U68JO4aG2ZgsixXuWYpRyNSd9J00jJT0H26x",
)

interface FormData {
  fullName: string
  planType: "monthly" // | "yearly" - Yearly commented out for now
}

interface SubscriptionPlan {
  id: number
  name: string
  price: number
  created_at: string
  formatted_price?: string
}

interface PaymentData {
  planId: number
  planType: "monthly" // | "yearly" - Yearly commented out for now
  fullName: string
  userId: number | undefined
  amount: number
  paymentMethodId: string
}

// Stripe card element change event type
interface StripeCardElementChangeEvent {
  error?: {
    message: string
    type: string
    code?: string
  }
  complete: boolean
  empty: boolean
  brand?: string
}

// Stripe card element options
const cardElementOptions = {
  style: {
    base: {
      fontSize: "14px",
      color: "#424770",
      "::placeholder": {
        color: "#aab7c4",
      },
      padding: "12px",
    },
    invalid: {
      color: "#9e2146",
    },
  },
  hidePostalCode: true, // Hide postal code field
}

const CheckoutForm = () => {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const dispatch = useDispatch<AppDispatch>()
  const user = useSelector(selectCurrentUser)
  const stripe = useStripe()
  const elements = useElements()

  const [formData, setFormData] = useState<FormData>({
    fullName: "",
    planType: "monthly",
  })

  const [selectedPlan, setSelectedPlan] = useState<SubscriptionPlan | null>(
    null,
  )
  const [plans, setPlans] = useState<SubscriptionPlan[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isProcessing, setIsProcessing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cardError, setCardError] = useState<string | null>(null)
  const [cardComplete, setCardComplete] = useState(false)

  // Get planId from URL params
  const planId = searchParams.get("planId")

  useEffect(() => {
    if (!user) {
      navigate("/login")
      return
    }

    if (!planId) {
      navigate("/console/membership")
      return
    }

    loadPlanData()
  }, [planId, user, navigate])

  const loadPlanData = async () => {
    try {
      setIsLoading(true)
      setError(null)

      const plansResponse = await dispatch(getSubscriptionPlan())
      let plansData: SubscriptionPlan[] = []

      if (plansResponse.payload && Array.isArray(plansResponse.payload)) {
        plansData = plansResponse.payload
      } else if (Array.isArray(plansResponse)) {
        plansData = plansResponse
      }

      setPlans(plansData)

      // Find the selected plan
      const plan = plansData.find((p) => p.id === parseInt(planId!))
      if (!plan) {
        setError("Plan not found")
        return
      }

      setSelectedPlan(plan)
    } catch (err) {
      console.error("Error loading plan data:", err)
      setError("Failed to load plan information")
    } finally {
      setIsLoading(false)
    }
  }

  const handleInputChange =
    (field: keyof FormData) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      setFormData({
        ...formData,
        [field]: event.target.value,
      })
    }

  const validateForm = (): boolean => {
    if (!formData.fullName.trim()) {
      setError("Full name is required")
      return false
    }

    if (!cardComplete) {
      setError("Please complete your card information")
      return false
    }

    if (cardError) {
      setError("Please fix card information errors")
      return false
    }

    return true
  }

  const calculatePrice = () => {
    if (!selectedPlan) return 0
    return selectedPlan.price
  }

  const formatPrice = (price: number) => {
    if (selectedPlan?.formatted_price && formData.planType === "monthly") {
      return selectedPlan.formatted_price
    }
    // Format price assuming it's stored in cents (divide by 100)
    return `$${(price / 100).toFixed(2)}`
  }

  const calculateNextRenewalDate = () => {
    const now = new Date()
    now.setMonth(now.getMonth() + 1)
    return now.toLocaleDateString()
  }

  const handleSubscribe = async () => {
    if (!validateForm() || !selectedPlan || !user || !stripe || !elements)
      return

    setIsProcessing(true)
    setError(null)
    setCardError(null)

    try {
      const cardElement = elements.getElement(CardElement)
      if (!cardElement) {
        throw new Error("Card element not found")
      }

      // Create payment method with Stripe
      const { error: stripeError, paymentMethod } =
        await stripe.createPaymentMethod({
          type: "card",
          card: cardElement,
          billing_details: {
            name: formData.fullName,
            email: user.email,
          },
        })

      if (stripeError) {
        setCardError(stripeError.message || "Card validation failed")
        return
      }

      if (!paymentMethod) {
        throw new Error("Failed to create payment method")
      }

      // Prepare payment data for your backend
      const paymentData: PaymentData = {
        planId: selectedPlan.id,
        planType: formData.planType,
        fullName: formData.fullName,
        userId: user.id,
        amount: calculatePrice(),
        paymentMethodId: paymentMethod.id,
      }

      // Send to your backend for processing
      const response = await fetch("/api/subscriptions/subscribe", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Authorization: `Bearer ${user.token}`, // Add auth if needed
        },
        body: JSON.stringify(paymentData),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.message || "Payment failed")
      }

      const result = await response.json()

      // Handle different response types from Stripe
      if (result.client_secret) {
        // Payment requires additional authentication (3D Secure)
        const { error: confirmError } = await stripe.confirmCardPayment(
          result.client_secret,
        )

        if (confirmError) {
          throw new Error(confirmError.message || "Payment confirmation failed")
        }
      }

      // Redirect to success page
      navigate("/console/subscription-success", {
        state: { subscription: result },
      })
    } catch (err) {
      console.error("Payment error:", err)
      setError(err instanceof Error ? err.message : "Payment processing failed")
    } finally {
      setIsProcessing(false)
    }
  }

  // Handle card element changes with proper typing
  const handleCardChange = (event: StripeCardElementChangeEvent) => {
    setCardError(event.error ? event.error.message : null)
    setCardComplete(event.complete)
  }

  if (!selectedPlan) return null

  const totalPrice = calculatePrice()

  // Styling - All styles declared first
  const containerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "5rem 2rem",
    fontFamily: "system-ui, -apple-system, sans-serif",
  }

  const checkoutContainerStyle: React.CSSProperties = {
    maxWidth: "500px",
    width: "100%",
  }

  const checkoutCardStyle: React.CSSProperties = {
    backgroundColor: "#e5e7eb",
    borderRadius: "1rem",
    padding: "2rem",
    display: "flex",
    flexDirection: "column",
    gap: "1.5rem",
  }

  const headerStyle: React.CSSProperties = {
    textAlign: "center",
    marginBottom: "1rem",
  }

  const headerTitleStyle: React.CSSProperties = {
    fontSize: "1.5rem",
    fontWeight: "bold",
    color: "#111827",
    margin: 0,
  }

  const planOptionStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    backgroundColor: "white",
    border: "2px solid #3b82f6",
    borderRadius: "0.5rem",
    padding: "1rem",
    gap: "1rem",
    marginBottom: "0.5rem",
  }

  const planDetailsStyle: React.CSSProperties = {
    flex: 1,
  }

  const planTitleStyle: React.CSSProperties = {
    fontWeight: "bold",
    color: "#111827",
    fontSize: "1.1rem",
    margin: 0,
  }

  const planPriceStyle: React.CSSProperties = {
    color: "#6b7280",
    fontSize: "0.9rem",
    margin: 0,
  }

  const sectionStyle: React.CSSProperties = {
    backgroundColor: "white",
    borderRadius: "0.5rem",
    padding: "1.5rem",
  }

  const sectionTitleStyle: React.CSSProperties = {
    fontSize: "1.25rem",
    fontWeight: "bold",
    color: "#111827",
    marginBottom: "1rem",
    margin: "0 0 1rem 0",
  }

  const orderRowStyle: React.CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: "0.5rem",
  }

  const orderItemTitleStyle: React.CSSProperties = {
    color: "#111827",
    fontSize: "0.95rem",
    margin: 0,
  }

  const orderItemSubtitleStyle: React.CSSProperties = {
    color: "#6b7280",
    fontSize: "0.85rem",
    margin: 0,
  }

  const orderPriceStyle: React.CSSProperties = {
    color: "#111827",
    fontSize: "0.95rem",
    margin: 0,
  }

  const dividerStyle: React.CSSProperties = {
    border: "none",
    borderTop: "1px solid #e5e7eb",
    margin: "1rem 0",
  }

  const noticeStyle: React.CSSProperties = {
    display: "flex",
    gap: "0.5rem",
    alignItems: "flex-start",
  }

  const noticeTextStyle: React.CSSProperties = {
    color: "#6b7280",
    fontSize: "0.85rem",
    lineHeight: 1.4,
    margin: 0,
  }

  const formGridStyle: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: "1rem",
    marginBottom: "1rem",
  }

  const inputStyle: React.CSSProperties = {
    padding: "0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    fontSize: "0.875rem",
    fontFamily: "inherit",
  }

  const cardElementContainerStyle: React.CSSProperties = {
    padding: "0.75rem",
    border: "1px solid #d1d5db",
    borderRadius: "0.375rem",
    backgroundColor: "white",
  }

  const disclaimerStyle: React.CSSProperties = {
    color: "#6b7280",
    fontSize: "0.75rem",
    lineHeight: 1.4,
    marginTop: "1rem",
    margin: "1rem 0 0 0",
  }

  const subscribeButtonStyle: React.CSSProperties = {
    backgroundColor: isProcessing ? "#9ca3af" : "#3b82f6",
    color: "white",
    padding: "0.75rem",
    borderRadius: "0.5rem",
    fontWeight: "500",
    fontSize: "1rem",
    border: "none",
    width: "100%",
    cursor: isProcessing ? "not-allowed" : "pointer",
    transition: "background-color 0.2s",
  }

  const errorStyle: React.CSSProperties = {
    color: "#dc2626",
    fontSize: "0.875rem",
    textAlign: "center",
    marginBottom: "1rem",
  }

  const cardErrorStyle: React.CSSProperties = {
    color: "#dc2626",
    fontSize: "0.75rem",
    marginTop: "0.5rem",
  }

  // Loading state
  if (isLoading) {
    return (
      <div style={containerStyle}>
        <div style={checkoutContainerStyle}>
          <div style={checkoutCardStyle}>
            <div style={headerStyle}>
              <h2 style={headerTitleStyle}>Loading...</h2>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // Error state
  if (error && !selectedPlan) {
    return (
      <div style={containerStyle}>
        <div style={checkoutContainerStyle}>
          <div style={checkoutCardStyle}>
            <div style={headerStyle}>
              <h2 style={headerTitleStyle}>Error</h2>
              <p style={{ color: "#dc2626", textAlign: "center" }}>{error}</p>
              <button
                onClick={() => navigate("/console/membership")}
                style={{
                  ...subscribeButtonStyle,
                  backgroundColor: "#6b7280",
                }}
              >
                Back to Plans
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={containerStyle}>
      <div style={checkoutContainerStyle}>
        <div style={checkoutCardStyle}>
          <div style={headerStyle}>
            <h2 style={headerTitleStyle}>{selectedPlan.name} Plan</h2>
          </div>

          {/* Plan Selection - Only Monthly for now */}
          <div>
            <div style={planOptionStyle}>
              <input
                type="radio"
                checked={formData.planType === "monthly"}
                onChange={() =>
                  setFormData({ ...formData, planType: "monthly" })
                }
                style={{ accentColor: "#3b82f6" }}
              />
              <div style={planDetailsStyle}>
                <h3 style={planTitleStyle}>Monthly</h3>
                <p style={planPriceStyle}>
                  {formatPrice(selectedPlan.price)}/month + tax
                </p>
              </div>
            </div>
          </div>

          {/* Order Details */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Order Details</h3>

            <div style={orderRowStyle}>
              <div>
                <p style={orderItemTitleStyle}>{selectedPlan.name} plan</p>
                <p style={orderItemSubtitleStyle}>Monthly</p>
              </div>
              <p style={orderPriceStyle}>{formatPrice(totalPrice)}</p>
            </div>

            <hr style={dividerStyle} />

            <div style={orderRowStyle}>
              <p style={orderItemTitleStyle}>Subtotal</p>
              <p style={orderPriceStyle}>{formatPrice(totalPrice)}</p>
            </div>

            <div style={orderRowStyle}>
              <p style={{ ...orderItemTitleStyle, fontWeight: "bold" }}>
                Total
              </p>
              <p style={{ ...orderPriceStyle, fontWeight: "bold" }}>
                {formatPrice(totalPrice)}
              </p>
            </div>
          </div>

          {/* Renewal Notice */}
          <div style={noticeStyle}>
            <span style={{ color: "#6b7280", fontSize: "1.2rem" }}>ℹ️</span>
            <p style={noticeTextStyle}>
              Your subscription will auto renew on {calculateNextRenewalDate()}.
              You will be charged {formatPrice(totalPrice)} (plus applicable
              taxes)
            </p>
          </div>

          {/* Error Display */}
          {error && <div style={errorStyle}>{error}</div>}

          {/* Payment Method with Stripe */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Payment Method</h3>

            <div style={formGridStyle}>
              <input
                type="text"
                placeholder="Full Name"
                value={formData.fullName}
                onChange={handleInputChange("fullName")}
                style={inputStyle}
                disabled={isProcessing}
              />

              {/* Stripe Card Element */}
              <div style={cardElementContainerStyle}>
                <CardElement
                  options={cardElementOptions}
                  onChange={handleCardChange}
                />
              </div>
              {cardError && <div style={cardErrorStyle}>{cardError}</div>}
            </div>

            <p style={disclaimerStyle}>
              By providing your payment information, you allow{" "}
              {process.env.REACT_APP_COMPANY_NAME || "Our Company"} to charge
              your card for future payments in accordance with their terms. You
              can cancel at any time.
            </p>
          </div>

          {/* Subscribe Button */}
          <button
            style={subscribeButtonStyle}
            onClick={handleSubscribe}
            disabled={isProcessing || !stripe}
            onMouseEnter={(e) => {
              if (!isProcessing && stripe) {
                ;(e.target as HTMLButtonElement).style.backgroundColor =
                  "#2563eb"
              }
            }}
            onMouseLeave={(e) => {
              if (!isProcessing && stripe) {
                ;(e.target as HTMLButtonElement).style.backgroundColor =
                  "#3b82f6"
              }
            }}
          >
            {isProcessing
              ? "Processing..."
              : !stripe
                ? "Loading..."
                : "Subscribe"}
          </button>
        </div>
      </div>
    </div>
  )
}

// Main component that wraps CheckoutForm with Stripe Elements
const PremiumCheckout = () => {
  return (
    <Elements stripe={stripePromise}>
      <CheckoutForm />
    </Elements>
  )
}

export default PremiumCheckout
