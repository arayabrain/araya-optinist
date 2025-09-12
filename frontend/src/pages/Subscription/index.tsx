import { useEffect, useState } from "react"
import { useDispatch, useSelector } from "react-redux"
import { useNavigate } from "react-router-dom"

import CheckIcon from "@mui/icons-material/Check"
import {
  Box,
  Button,
  styled,
  Typography,
  CircularProgress,
  Chip,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from "@mui/material"

import {
  getSubscriptionPlan,
  getUserSubscription,
  createCheckoutSession,
} from "store/slice/Subscriptions/SubscriptionActions"
import {
  selectSubscriptionPlans,
  selectUserSubscription,
  selectSubscriptionLoading,
  selectSubscriptionError,
  selectIsSubscriptionExpired,
  selectCurrentPlanId,
  selectCheckoutLoading,
} from "store/slice/Subscriptions/SubscriptionSelector"
import { clearError } from "store/slice/Subscriptions/SubscriptionSlice"
import type {
  SubscriptionPlan,
  PlanFeature,
} from "store/slice/Subscriptions/SubscriptionType"
import { selectCurrentUser } from "store/slice/User/UserSelector"
import { AppDispatch } from "store/store"
import {
  getBillingCycleText,
  getCurrencySymbol,
  getPlanFeatures,
} from "utils/subscriptions/SubscriptionUtils"

const SubscriptionPlans = () => {
  const user = useSelector(selectCurrentUser)
  const plans = useSelector(selectSubscriptionPlans)
  const userSubscription = useSelector(selectUserSubscription)
  const loading = useSelector(selectSubscriptionLoading)
  const error = useSelector(selectSubscriptionError)
  const isSubscriptionExpired = useSelector(selectIsSubscriptionExpired)
  const currentPlanId = useSelector(selectCurrentPlanId)

  const dispatch = useDispatch<AppDispatch>()
  const navigate = useNavigate()

  // State for downgrade confirmation dialog
  const [showDowngradeDialog, setShowDowngradeDialog] = useState(false)
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(null)
  const [processingPlanId, setProcessingPlanId] = useState<number | null>(null) // Track which plan is being processed

  // Fetch data on component mount
  useEffect(() => {
    const loadData = async () => {
      dispatch(clearError())
      dispatch(getSubscriptionPlan())

      if (user?.id) {
        dispatch(getUserSubscription(user.id))
      }
    }

    loadData()
  }, [dispatch, user?.id])

  // Check if user has a specific plan
  const isCurrentPlan = (planId: number) => {
    return currentPlanId === planId
  }

  // Check if the selected plan is a downgrade (free plan)
  const isDowngrade = (planId: number) => {
    const plan = plans.find((p) => p.id === planId)
    return plan?.price === 0
  }

  enum SUBSCRIPTION_PLAN {
    FREE = "Free",
    PREMIUM = "Premium",
  }

  const handleUpgradeClick = async (planId: number) => {
    // Check if it's a downgrade (free plan)
    if (isDowngrade(planId)) {
      setSelectedPlanId(planId)
      setShowDowngradeDialog(true)
    } else {
      // For upgrades, create checkout session and redirect to Stripe
      if (!user?.id) {
        // Handle case where user is not logged in
        navigate("/login")
        return
      }

      try {
        setProcessingPlanId(planId)

        // Dispatch the action to create checkout session
        const resultAction = await dispatch(createCheckoutSession(planId))

        // Check if the action was fulfilled
        if (createCheckoutSession.fulfilled.match(resultAction)) {
          const { checkout_url } = resultAction.payload

          // Redirect to Stripe checkout
          window.location.href = checkout_url
        } else {
          // Handle error case
          console.error(
            "Failed to create checkout session:",
            resultAction.error,
          )
          // You might want to show an error message to the user here
        }
      } catch (error) {
        console.error("Error creating checkout session:", error)
        // Handle error - maybe show a toast notification
      } finally {
        setProcessingPlanId(null)
      }
    }
  }

  const handleConfirmDowngrade = () => {
    if (selectedPlanId) {
      // Navigate to downgrade api
      // TODO: Implement the actual downgrade logic
    }
    setShowDowngradeDialog(false)
    setSelectedPlanId(null)
  }

  const handleCancelDowngrade = () => {
    setShowDowngradeDialog(false)
    setSelectedPlanId(null)
  }

  const getExpirationDate = () => {
    if (userSubscription?.expiration) {
      return new Date(userSubscription.expiration).toLocaleDateString()
    }
    return "N/A"
  }

  const handleRetry = () => {
    dispatch(clearError())
    dispatch(getSubscriptionPlan())
    if (user?.id) {
      dispatch(getUserSubscription(user.id))
    }
  }

  // Filter only active plans
  const activePlans = plans.filter((plan) => plan.status === true)

  // Get price display for a plan
  const getPriceDisplay = (plan: SubscriptionPlan) => {
    const currencySymbol = getCurrencySymbol(plan.currency)
    const billingCycle = getBillingCycleText(plan.billing_cycle)

    if (plan.price === 0) {
      return SUBSCRIPTION_PLAN.FREE
    }

    const basePrice = (plan.price / 100).toFixed(0)
    return `${currencySymbol}${basePrice}/${billingCycle}`
  }

  // Check if a specific plan is currently being processed
  const isPlanProcessing = (planId: number) => {
    return processingPlanId === planId
  }

  // Loading state
  if (loading) {
    return (
      <BoxWrapper>
        <CircularProgress />
        <Typography variant="body1" sx={{ mt: 2 }}>
          Loading subscription plans...
        </Typography>
      </BoxWrapper>
    )
  }

  // Error state
  if (error) {
    return (
      <BoxWrapper>
        <Typography variant="h6" color="error" sx={{ mb: 2 }}>
          Error loading subscription plans
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {typeof error === "string" ? error : "An unexpected error occurred"}
        </Typography>
        <Button variant="outlined" onClick={handleRetry} sx={{ mt: 2 }}>
          Retry
        </Button>
      </BoxWrapper>
    )
  }

  // No plans state
  if (activePlans.length === 0) {
    return (
      <BoxWrapper>
        <Typography variant="h6" color="text.secondary" sx={{ mb: 2 }}>
          No subscription plans available
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Please try again later or contact support.
        </Typography>
        <Button variant="outlined" onClick={handleRetry} sx={{ mt: 2 }}>
          Retry
        </Button>
      </BoxWrapper>
    )
  }

  return (
    <BoxWrapper>
      <SubscriptionTitle variant="h3">Subscription Plans</SubscriptionTitle>

      {/* Tax information notice */}
      <TaxNotice severity="info" sx={{ mb: 3, maxWidth: "600px" }}>
        <Typography variant="body2">
          <strong>Tax Information:</strong> Applicable taxes will be calculated
          automatically based on your location during checkout. Final price may
          include consumption tax.
        </Typography>
      </TaxNotice>

      {/* Current subscription status */}
      {userSubscription &&
        userSubscription.plan_name !== SUBSCRIPTION_PLAN.FREE && (
          <SubscriptionStatus>
            <Typography variant="body1">
              Current Plan: <strong>{userSubscription.plan_name}</strong>
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {isSubscriptionExpired ? (
                <span style={{ color: "#dc2626" }}>
                  Expired on{" "}
                  {new Date(userSubscription.expiration).toLocaleDateString()}
                </span>
              ) : (
                `Expires on ${new Date(userSubscription.expiration).toLocaleDateString()}`
              )}
            </Typography>
          </SubscriptionStatus>
        )}

      <SubscriptionWrapper>
        <SubscriptionContent>
          {activePlans.map((plan) => {
            const features = getPlanFeatures(plan)
            const isCurrent = isCurrentPlan(plan.id)
            const isFree = plan.price === 0
            const priceDisplay = getPriceDisplay(plan)
            const isProcessing = isPlanProcessing(plan.id)

            return (
              <PlanCard key={plan.id} isHighlighted={plan.name === "Premium"}>
                <PlanHeader>
                  <PlanTitle variant="h4">{plan.name}</PlanTitle>

                  <PriceContainer>
                    <PlanPrice variant="h5">{priceDisplay}</PlanPrice>
                    {!isFree && (
                      <Typography variant="caption" color="text.secondary">
                        + applicable taxes
                      </Typography>
                    )}
                  </PriceContainer>

                  {plan.billing_cycle === 2 && (
                    <Chip
                      label="Best Value"
                      color="primary"
                      size="small"
                      sx={{ mt: 1 }}
                    />
                  )}
                </PlanHeader>

                <FeaturesList>
                  {features.length > 0 ? (
                    features.map((feature: PlanFeature, index: number) => (
                      <FeatureItem key={index}>
                        <CheckIcon
                          sx={{
                            color: feature.isPremium ? "#16a34a" : "#6b7280",
                            fontSize: "1.25rem",
                          }}
                        />
                        <FeatureText isPremium={feature.isPremium}>
                          {feature.text}
                        </FeatureText>
                      </FeatureItem>
                    ))
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      No features available for this plan
                    </Typography>
                  )}
                </FeaturesList>

                <ButtonWrapper>
                  {isCurrent ? (
                    <CurrentPlanButton disabled>
                      {isSubscriptionExpired ? "Expired Plan" : "Current Plan"}
                    </CurrentPlanButton>
                  ) : (
                    <UpgradeButton
                      variant="contained"
                      onClick={() => handleUpgradeClick(plan.id)}
                      disabled={!user || isProcessing}
                      startIcon={
                        isProcessing ? <CircularProgress size={16} /> : null
                      }
                    >
                      {isProcessing
                        ? "Processing..."
                        : isFree
                          ? "Downgrade"
                          : "Upgrade"}
                    </UpgradeButton>
                  )}
                </ButtonWrapper>
              </PlanCard>
            )
          })}
        </SubscriptionContent>
      </SubscriptionWrapper>

      {/* Downgrade Confirmation Dialog */}
      <Dialog
        open={showDowngradeDialog}
        onClose={handleCancelDowngrade}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          <Typography variant="h6" component="div" sx={{ fontWeight: "bold" }}>
            Cancel Subscription
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Typography variant="body1" sx={{ mb: 2 }}>
            Are you sure you want to cancel your subscription? Your subscription
            will be canceled at <strong>{getExpirationDate()}</strong>.
          </Typography>
          <Typography variant="body2" color="text.secondary">
            You will lose access to premium features after this date, but you
            can resubscribe at any time.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={handleCancelDowngrade}
            variant="outlined"
            sx={{ mr: 1 }}
          >
            No
          </Button>
          <Button
            onClick={handleConfirmDowngrade}
            variant="contained"
            color="error"
            autoFocus
          >
            Yes
          </Button>
        </DialogActions>
      </Dialog>
    </BoxWrapper>
  )
}

// Styled Components remain the same...
const BoxWrapper = styled(Box)({
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  padding: "5rem 2rem",
})

const SubscriptionTitle = styled(Typography)(() => ({
  fontWeight: "bold",
  color: "#111827",
  marginBottom: "2rem",
  textAlign: "center",
}))

const TaxNotice = styled(Alert)(() => ({
  borderRadius: "0.75rem",
  "& .MuiAlert-message": {
    width: "100%",
  },
}))

const SubscriptionStatus = styled(Box)(() => ({
  backgroundColor: "#f8fafc",
  border: "1px solid #e2e8f0",
  borderRadius: "0.5rem",
  padding: "1rem 1.5rem",
  marginBottom: "2rem",
  textAlign: "center",
  maxWidth: "400px",
}))

const SubscriptionWrapper = styled(Box)(() => ({
  width: "100%",
  maxWidth: "64rem",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
}))

const SubscriptionContent = styled(Box)(() => ({
  display: "flex",
  flexDirection: "row",
  gap: "2rem",
  width: "100%",
  "@media (max-width: 768px)": {
    flexDirection: "column",
  },
}))

const PlanCard = styled(Box, {
  shouldForwardProp: (prop) => prop !== "isHighlighted",
})<{ isHighlighted?: boolean }>(({ isHighlighted }) => ({
  flex: 1,
  backgroundColor: "white",
  borderRadius: "1rem",
  border: isHighlighted ? "2px solid #3b82f6" : "2px solid #dbeafe",
  padding: "2rem",
  boxShadow: isHighlighted
    ? "0 10px 25px -3px rgba(59, 130, 246, 0.1)"
    : "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
  display: "flex",
  flexDirection: "column",
  position: "relative",
  transform: isHighlighted ? "scale(1.05)" : "scale(1)",
  transition: "transform 0.2s ease-in-out",
}))

const PlanHeader = styled(Box)(() => ({
  textAlign: "center",
  marginBottom: "2rem",
}))

const PlanTitle = styled(Typography)(() => ({
  fontWeight: "bold",
  color: "#111827",
  marginBottom: "0.5rem",
}))

const PriceContainer = styled(Box)(() => ({
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: "0.25rem",
}))

const PlanPrice = styled(Typography)(() => ({
  fontWeight: "600",
  color: "#3b82f6",
}))

const FeaturesList = styled(Box)(() => ({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  marginBottom: "3rem",
  flex: 1,
}))

const FeatureItem = styled(Box)(() => ({
  display: "flex",
  alignItems: "center",
  gap: "0.75rem",
}))

const FeatureText = styled(Typography, {
  shouldForwardProp: (prop) => prop !== "isPremium",
})<{ isPremium?: boolean }>(({ isPremium }) => ({
  color: isPremium ? "#16a34a" : "#374151",
  fontSize: "1rem",
  fontWeight: isPremium ? "500" : "400",
}))

const ButtonWrapper = styled(Box)(() => ({
  textAlign: "center",
}))

const CurrentPlanButton = styled(Button)(() => ({
  backgroundColor: "#f3f4f6",
  color: "#6b7280",
  padding: "0.75rem 1.5rem",
  borderRadius: "0.5rem",
  fontWeight: "500",
  width: "100%",
  textTransform: "none",
  "&:disabled": {
    backgroundColor: "#f3f4f6",
    color: "#6b7280",
  },
}))

const UpgradeButton = styled(Button)(() => ({
  backgroundColor: "#3b82f6",
  color: "white",
  padding: "0.75rem 2rem",
  borderRadius: "0.5rem",
  fontWeight: "500",
  width: "100%",
  textTransform: "none",
  "&:hover": {
    backgroundColor: "#2563eb",
  },
  "&:disabled": {
    backgroundColor: "#9ca3af",
    color: "#ffffff",
  },
}))

export default SubscriptionPlans
