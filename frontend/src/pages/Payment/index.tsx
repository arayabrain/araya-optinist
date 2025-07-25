import { useState, useEffect } from "react"
import { useDispatch, useSelector } from "react-redux"
import { useSearchParams, useNavigate } from "react-router-dom"

import { getSubscriptionPlan } from "store/slice/Subscriptions/SubscriptionActions"
import { selectCurrentUser } from "store/slice/User/UserSelector"
import { AppDispatch } from "store/store"

interface FormData {
  fullName: string
  cardNumber: string
  expirationDate: string
  securityCode: string
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
  cardNumber: string
  expirationDate: string
  securityCode: string
  userId: number | undefined
  amount: number
}

const PremiumCheckout = () => {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const dispatch = useDispatch<AppDispatch>()
  const user = useSelector(selectCurrentUser)

  const [formData, setFormData] = useState<FormData>({
    fullName: "",
    cardNumber: "",
    expirationDate: "",
    securityCode: "",
    planType: "monthly",
  })

  const [selectedPlan, setSelectedPlan] = useState<SubscriptionPlan | null>(
    null,
  )
  const [plans, setPlans] = useState<SubscriptionPlan[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isProcessing, setIsProcessing] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      let value = event.target.value

      // Handle expiration date formatting (MM/YY)
      if (field === "expirationDate") {
        value = value.replace(/\D/g, "")
        if (value.length > 4) {
          value = value.slice(0, 4)
        }
        if (value.length >= 2) {
          const month = value.slice(0, 2)
          const year = value.slice(2)
          const monthNum = parseInt(month)
          if (monthNum > 12) {
            return
          }
          value = month + (year ? "/" + year : "")
        }
      }

      // Handle security code (3-4 digits only)
      if (field === "securityCode") {
        value = value.replace(/\D/g, "")
        if (value.length > 4) {
          value = value.slice(0, 4)
        }
      }

      // Handle card number formatting (spaces every 4 digits)
      if (field === "cardNumber") {
        value = value.replace(/\D/g, "")
        if (value.length > 16) {
          value = value.slice(0, 16)
        }
        value = value.replace(/(\d{4})(?=\d)/g, "$1 ")
      }

      setFormData({
        ...formData,
        [field]: value,
      })
    }

  const validateForm = (): boolean => {
    if (!formData.fullName.trim()) {
      setError("Full name is required")
      return false
    }

    const cardNumberDigits = formData.cardNumber.replace(/\s/g, "")
    if (cardNumberDigits.length !== 16) {
      setError("Please enter a valid 16-digit card number")
      return false
    }

    if (!formData.expirationDate.match(/^\d{2}\/\d{2}$/)) {
      setError("Please enter expiration date in MM/YY format")
      return false
    }

    // Validate expiration date is not in the past
    const [month, year] = formData.expirationDate.split("/")
    const currentDate = new Date()
    const currentYear = currentDate.getFullYear() % 100
    const currentMonth = currentDate.getMonth() + 1

    const expYear = parseInt(year)
    const expMonth = parseInt(month)

    if (
      expYear < currentYear ||
      (expYear === currentYear && expMonth < currentMonth)
    ) {
      setError("Card has expired")
      return false
    }

    if (formData.securityCode.length < 3) {
      setError("Please enter a valid security code")
      return false
    }

    return true
  }

  const calculatePrice = () => {
    if (!selectedPlan) return 0

    // Monthly pricing only for now
    // if (formData.planType === "yearly") {
    //   return selectedPlan.price * 12 * 0.8 // 20% discount for yearly
    // }

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
    // Only monthly for now
    // if (formData.planType === "yearly") {
    //   now.setFullYear(now.getFullYear() + 1)
    // } else {
    now.setMonth(now.getMonth() + 1)
    // }
    return now.toLocaleDateString()
  }

  const handleSubscribe = async () => {
    if (!validateForm() || !selectedPlan || !user) return

    setIsProcessing(true)
    setError(null)

    try {
      const paymentData: PaymentData = {
        planId: selectedPlan.id,
        planType: formData.planType,
        fullName: formData.fullName,
        cardNumber: formData.cardNumber.replace(/\s/g, ""), // Remove spaces
        expirationDate: formData.expirationDate,
        securityCode: formData.securityCode,
        userId: user.id,
        amount: calculatePrice(),
      }

      // TODO: Replace with your actual payment processing endpoint
      const response = await fetch("/api/subscriptions/subscribe", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Add auth headers if needed
        },
        body: JSON.stringify(paymentData),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.message || "Payment failed")
      }

      const result = await response.json()

      // Redirect to success page or dashboard
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

  const halfWidthContainerStyle: React.CSSProperties = {
    display: "flex",
    gap: "1rem",
  }

  const fullWidthInputStyle: React.CSSProperties = {
    ...inputStyle,
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

            {/* Yearly option commented out for now */}
            {/* <div style={planOptionStyle}>
              <input
                type="radio"
                checked={formData.planType === "yearly"}
                onChange={() =>
                  setFormData({ ...formData, planType: "yearly" })
                }
                style={{ accentColor: "#3b82f6" }}
              />
              <div style={planDetailsStyle}>
                <h3 style={planTitleStyle}>Yearly</h3>
                <p style={planPriceStyle}>
                  {formatPrice(selectedPlan.price * 12 * 0.8)}/year + tax
                  <span
                    style={{
                      color: "#16a34a",
                      fontWeight: "bold",
                      marginLeft: "0.5rem",
                    }}
                  >
                    (Save 20%)
                  </span>
                </p>
              </div>
            </div> */}
          </div>

          {/* Order Details */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Order Details</h3>

            <div style={orderRowStyle}>
              <div>
                <p style={orderItemTitleStyle}>{selectedPlan.name} plan</p>
                <p style={orderItemSubtitleStyle}>
                  {/* {formData.planType === "yearly" ? "Yearly" : "Monthly"} */}
                  Monthly
                </p>
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

          {/* Payment Method */}
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

              <input
                type="text"
                placeholder="Card Number"
                value={formData.cardNumber}
                onChange={handleInputChange("cardNumber")}
                style={fullWidthInputStyle}
                disabled={isProcessing}
              />

              <div style={halfWidthContainerStyle}>
                <input
                  type="text"
                  placeholder="MM/YY"
                  value={formData.expirationDate}
                  onChange={handleInputChange("expirationDate")}
                  style={inputStyle}
                  disabled={isProcessing}
                />

                <input
                  type="text"
                  placeholder="Security Code"
                  value={formData.securityCode}
                  onChange={handleInputChange("securityCode")}
                  style={inputStyle}
                  disabled={isProcessing}
                />
              </div>
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
            disabled={isProcessing}
            onMouseEnter={(e) => {
              if (!isProcessing) {
                ;(e.target as HTMLButtonElement).style.backgroundColor =
                  "#2563eb"
              }
            }}
            onMouseLeave={(e) => {
              if (!isProcessing) {
                ;(e.target as HTMLButtonElement).style.backgroundColor =
                  "#3b82f6"
              }
            }}
          >
            {isProcessing ? "Processing..." : "Subscribe"}
          </button>
        </div>
      </div>
    </div>
  )
}

export default PremiumCheckout
