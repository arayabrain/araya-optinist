import React, { useState, useEffect } from "react"
import { useDispatch, useSelector } from "react-redux"
import { useNavigate } from "react-router-dom"

import ArrowBackIcon from "@mui/icons-material/ArrowBack"
import {
  Box,
  Typography,
  Button,
  IconButton,
  Alert,
  Skeleton,
} from "@mui/material"
import { styled } from "@mui/material/styles"

import { InvoiceDTO } from "api/paymentMethod/PaymentMethodApiDTO"
import Loading from "components/common/Loading"
import CardBrandIcon from "pages/Invoice/CardBrandIcon"
import {
  getDefaultPaymentMethod,
  getUserInvoices,
} from "store/slice/PaymentMethod/PaymentMethodActions"
import {
  selectDefaultPaymentMethod,
  selectDefaultPaymentMethodLoading,
  selectInvoices,
  selectInvoicesLoading,
  selectFirstPaymentMethodsError,
} from "store/slice/PaymentMethod/PaymentMethodSelector"
import {
  getUserSubscription,
  getUTCServerTime,
} from "store/slice/Subscriptions/SubscriptionActions"
import {
  selectUserSubscription,
  selectUserSubscriptionLoading,
  selectSubscriptionError,
} from "store/slice/Subscriptions/SubscriptionSelector"
import { selectCurrentUserId } from "store/slice/User/UserSelector"
import { AppDispatch } from "store/store"

type CardBrand =
  | "visa"
  | "mastercard"
  | "amex"
  | "discover"
  | "jcb"
  | "diners"
  | "unionpay"

// Styled Components (keeping all existing styles)
const Container = styled(Box)(() => ({
  minHeight: "100vh",
  padding: "24px",
  position: "relative", // Added for Loading component positioning
}))

const MainWrapper = styled(Box)(() => ({
  maxWidth: "1024px",
  margin: "0 auto",
}))

const MainContainer = styled(Box)(() => ({
  background: "white",
  borderRadius: "12px",
  border: "4px solid #3b82f6",
  padding: "32px",
  boxShadow: "0 10px 25px -3px rgba(0, 0, 0, 0.1)",
}))

const Section = styled(Box)(() => ({
  backgroundColor: "#f1f5f9",
  borderRadius: "12px",
  padding: "32px",
  marginBottom: "32px",
}))

const FlexContainer = styled(Box)(() => ({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
}))

const FlexRow = styled(Box)(() => ({
  display: "flex",
  alignItems: "center",
  gap: "16px",
}))

const PlanTitle = styled(Typography)(() => ({
  fontSize: "24px",
  fontWeight: "bold",
  color: "#111827",
  margin: 0,
}))

const PlanType = styled(Typography)(() => ({
  color: "#6b7280",
  margin: "4px 0",
}))

const ExpirationText = styled(Typography)(() => ({
  color: "#374151",
  marginTop: "4px",
}))

const PrimaryButton = styled(Button)(() => ({
  background: "#2563eb",
  color: "white",
  padding: "12px 24px",
  borderRadius: "8px",
  border: "none",
  fontWeight: "500",
  cursor: "pointer",
  textTransform: "none",
  transition: "background-color 0.2s",
  "&:hover": {
    background: "#1d4ed8",
  },
  "&:disabled": {
    background: "#9ca3af",
    cursor: "not-allowed",
  },
}))

const PaymentTitle = styled(Typography)(() => ({
  fontSize: "20px",
  fontWeight: "bold",
  color: "#111827",
  marginBottom: "16px",
}))

interface CardIconProps {
  brand?: string
}

const CardIcon = styled(Box, {
  shouldForwardProp: (prop) => prop !== "brand",
})<CardIconProps>(() => ({
  width: "32px",
  height: "32px",
  borderRadius: "4px",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
}))

const CardNumber = styled(Typography)(() => ({
  color: "#374151",
}))

const InvoicesTitle = styled(Typography)(() => ({
  fontSize: "20px",
  fontWeight: "bold",
  color: "#111827",
  marginBottom: "24px",
}))

const TableContainer = styled(Box)(() => ({
  borderRadius: "12px",
  border: "1px solid #e5e7eb",
  overflow: "hidden",
}))

const Table = styled("table")(() => ({
  width: "100%",
  background: "white",
  borderCollapse: "collapse",
  textAlign: "center",
}))

const TableHeader = styled("thead")(() => ({
  background: "#f9fafb",
}))

const TableHeaderCell = styled("th")(() => ({
  padding: "16px 24px",
  textAlign: "center",
  fontSize: "14px",
  fontWeight: "600",
  color: "#111827",
  borderBottom: "1px solid #e5e7eb",
}))

const TableCell = styled("td")(() => ({
  padding: "16px 24px",
  fontSize: "14px",
  color: "#111827",
}))

const TableRow = styled("tr")(() => ({
  "&:not(:last-child) td": {
    borderBottom: "1px solid #e5e7eb",
  },
}))

const BackButton = styled(IconButton)(() => ({
  position: "absolute",
  top: "20px",
  left: "24px",
  backgroundColor: "white",
  border: "1px solid #e5e7eb",
  borderRadius: "8px",
  padding: "8px",
  color: "#374151",
  boxShadow: "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
  zIndex: 10,
  "&:hover": {
    backgroundColor: "#f9fafb",
    borderColor: "#d1d5db",
  },
  "&:disabled": {
    backgroundColor: "#f3f4f6",
    color: "#9ca3af",
    cursor: "not-allowed",
  },
}))

const ViewButton = styled(Button)(() => ({
  background: "#2563eb",
  color: "white",
  padding: "6px 16px",
  borderRadius: "4px",
  border: "none",
  fontSize: "14px",
  fontWeight: "500",
  cursor: "pointer",
  textTransform: "none",
  transition: "background-color 0.2s",
  "&:hover": {
    background: "#1d4ed8",
  },
  "&:disabled": {
    background: "#9ca3af",
    cursor: "not-allowed",
  },
}))

// Helper functions
function formatCardBrand(brand?: string): string {
  const brandNames: Record<CardBrand, string> = {
    visa: "Visa",
    mastercard: "Mastercard",
    amex: "American Express",
    discover: "Discover",
    jcb: "JCB",
    diners: "Diners Club",
    unionpay: "UnionPay",
  }

  if (!brand) return "Unknown"

  const normalizedBrand = brand.toLowerCase() as CardBrand
  return brandNames[normalizedBrand] || brand
}

const InvoicesPage: React.FC = () => {
  const navigate = useNavigate()

  const dispatch = useDispatch<AppDispatch>()

  // Redux selectors - separate slices
  const subscription = useSelector(selectUserSubscription)
  const subscriptionLoading = useSelector(selectUserSubscriptionLoading)
  const subscriptionError = useSelector(selectSubscriptionError)

  const paymentMethod = useSelector(selectDefaultPaymentMethod)
  const paymentMethodLoading = useSelector(selectDefaultPaymentMethodLoading)

  const invoices = useSelector(selectInvoices)
  const invoicesLoading = useSelector(selectInvoicesLoading)

  const paymentMethodsError = useSelector(selectFirstPaymentMethodsError)

  // Local state for loading management
  const [isInitialLoading, setIsInitialLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [serverTimeDate, setServerTimeDate] = useState<Date>(new Date())

  const userId = useSelector(selectCurrentUserId)

  // Combined loading states
  const shouldShowLoader = isInitialLoading || isRefreshing
  const error = subscriptionError || paymentMethodsError

  // Load data on component mount
  useEffect(() => {
    const loadData = async (): Promise<void> => {
      try {
        setIsInitialLoading(true)

        // Dispatch all actions concurrently
        await Promise.all([
          dispatch(getUserSubscription()),
          dispatch(getDefaultPaymentMethod()),
          dispatch(getUserInvoices(userId)),
        ])
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("Error loading data:", err)
      } finally {
        setIsInitialLoading(false)
      }
    }

    if (userId) {
      loadData()
    }
  }, [dispatch, userId])

  useEffect(() => {
    const fetchServerTime = async (): Promise<void> => {
      try {
        const response = await dispatch(getUTCServerTime())
        // Get current UTC time from server
        if (
          !response.payload ||
          !(response.payload as { server_time: string }).server_time
        ) {
          throw new Error("Server time not available")
        }
        const fetchedServerTime = new Date(
          (response.payload as { server_time: string }).server_time,
        )
        setServerTimeDate(fetchedServerTime)
      } catch (err) {
        console.error("Error fetching server time:", err)
      }
    }

    fetchServerTime()
  }, [dispatch])

  // Refresh data function
  const refreshData = async (): Promise<void> => {
    try {
      setIsRefreshing(true)

      await Promise.all([
        dispatch(getUserSubscription()),
        dispatch(getDefaultPaymentMethod()),
        dispatch(getUserInvoices(userId)),
      ])
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("Error refreshing data:", err)
    } finally {
      setIsRefreshing(false)
    }
  }

  const handleGoBack = (): void => {
    if (!shouldShowLoader) {
      navigate("/console/account")
    }
  }

  const handleAdjustPlan = (): void => {
    if (!shouldShowLoader) {
      navigate("/console/subscription")
    }
  }

  const handleManageBilling = async (): Promise<void> => {
    if (!shouldShowLoader) {
      window.open(
        "https://billing.stripe.com/p/login/test_5kQ9ATdaS2TbdknghI2wU00",
        "_blank",
      )
    }
  }

  const handleViewInvoice = (invoice: InvoiceDTO): void => {
    if (!shouldShowLoader && invoice.invoice_url) {
      window.open(invoice.invoice_url, "_blank")
    }
  }

  const formatDate = (dateString: string): string => {
    const date = new Date(dateString)
    return date.toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    })
  }

  if (error && !shouldShowLoader) {
    return (
      <Container>
        <BackButton onClick={handleGoBack} aria-label="Go back">
          <ArrowBackIcon />
        </BackButton>
        <MainWrapper>
          <Alert
            severity="error"
            sx={{ mt: 4 }}
            action={
              <Button
                color="inherit"
                size="small"
                onClick={refreshData}
                disabled={isRefreshing}
              >
                {isRefreshing ? "Retrying..." : "Retry"}
              </Button>
            }
          >
            {error}
          </Alert>
        </MainWrapper>
        <Loading loading={isRefreshing} position="fixed" />
      </Container>
    )
  }

  return (
    <Container>
      {/* Loading Component */}
      <Loading loading={shouldShowLoader} position="fixed" />

      <BackButton
        onClick={handleGoBack}
        aria-label="Go back"
        disabled={shouldShowLoader}
      >
        <ArrowBackIcon />
      </BackButton>

      <MainWrapper>
        <MainContainer>
          {/* Premium Plan Section */}
          <Section>
            <FlexContainer>
              <FlexRow>
                <Box
                  component="img"
                  src="/static/optinist_logo.png"
                  alt="OPTINIST Logo"
                  sx={{
                    width: "84px",
                    height: "84px",
                    marginRight: "16px",
                    opacity: shouldShowLoader ? 0.5 : 1,
                  }}
                />
                <Box>
                  {subscriptionLoading && !shouldShowLoader ? (
                    <>
                      <Skeleton variant="text" width={200} height={32} />
                      <Skeleton variant="text" width={100} height={24} />
                      <Skeleton variant="text" width={300} height={20} />
                    </>
                  ) : subscription ? (
                    <>
                      <PlanTitle>
                        {subscription.plan_name || "Premium Plan"}
                      </PlanTitle>
                      <PlanType>Monthly</PlanType>
                      <ExpirationText>
                        Your subscription{" "}
                        {new Date(subscription.expiration) < serverTimeDate
                          ? "expired on"
                          : subscription.scheduled_downgrade
                            ? "will expire on"
                            : "will renew on"}{" "}
                        {formatDate(subscription.expiration)}
                      </ExpirationText>
                    </>
                  ) : (
                    <>
                      <PlanTitle>No Active Subscription</PlanTitle>
                      <PlanType>-</PlanType>
                      <ExpirationText>
                        No active subscription found
                      </ExpirationText>
                    </>
                  )}
                </Box>
              </FlexRow>

              <PrimaryButton
                onClick={handleAdjustPlan}
                disabled={shouldShowLoader}
              >
                {subscription
                  ? new Date(subscription.expiration) < serverTimeDate
                    ? "Upgrade"
                    : "Downgrade"
                  : "Subscribe Now"}
              </PrimaryButton>
            </FlexContainer>
          </Section>

          {/* Payment Section */}
          <Section>
            <FlexContainer>
              <Box>
                <PaymentTitle>Payment Method</PaymentTitle>
                {paymentMethodLoading && !shouldShowLoader ? (
                  <FlexRow>
                    <Skeleton
                      variant="rectangular"
                      width={32}
                      height={24}
                      sx={{ borderRadius: "4px", mr: 1.5 }}
                    />
                    <Skeleton variant="text" width={150} height={24} />
                  </FlexRow>
                ) : paymentMethod ? (
                  <FlexRow>
                    <CardIcon brand={paymentMethod.brand}>
                      <CardBrandIcon brand={paymentMethod.brand} size={32} />
                    </CardIcon>
                    <CardNumber>
                      {formatCardBrand(paymentMethod.brand)} ••••••
                      {paymentMethod.last4}
                    </CardNumber>
                  </FlexRow>
                ) : (
                  <CardNumber>No payment method on file</CardNumber>
                )}
              </Box>

              <PrimaryButton
                onClick={handleManageBilling}
                disabled={shouldShowLoader}
              >
                Manage Billing
              </PrimaryButton>
            </FlexContainer>
          </Section>

          {/* Invoices Section */}
          <Box>
            <InvoicesTitle>Invoices</InvoicesTitle>

            <TableContainer>
              <Table>
                <TableHeader>
                  <tr>
                    <TableHeaderCell>Date</TableHeaderCell>
                    <TableHeaderCell>Total</TableHeaderCell>
                    <TableHeaderCell>Status</TableHeaderCell>
                    <TableHeaderCell>Actions</TableHeaderCell>
                  </tr>
                </TableHeader>
                <tbody>
                  {invoicesLoading && !shouldShowLoader ? (
                    Array.from({ length: 3 }, (_, index) => (
                      <TableRow key={`loading-${index}`}>
                        <TableCell>
                          <Skeleton variant="text" />
                        </TableCell>
                        <TableCell>
                          <Skeleton variant="text" />
                        </TableCell>
                        <TableCell>
                          <Skeleton variant="text" />
                        </TableCell>
                        <TableCell>
                          <Skeleton
                            variant="rectangular"
                            width={60}
                            height={32}
                          />
                        </TableCell>
                      </TableRow>
                    ))
                  ) : invoices.length > 0 ? (
                    invoices.map((invoice: InvoiceDTO) => (
                      <TableRow key={invoice.id}>
                        <TableCell>{formatDate(invoice.date)}</TableCell>
                        <TableCell>{invoice.total}</TableCell>
                        <TableCell>{invoice.status}</TableCell>
                        <TableCell>
                          <ViewButton
                            onClick={() => handleViewInvoice(invoice)}
                            disabled={shouldShowLoader}
                          >
                            View
                          </ViewButton>
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={4}>
                        <Typography color="text.secondary">
                          No invoices found
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </tbody>
              </Table>
            </TableContainer>
          </Box>
        </MainContainer>
      </MainWrapper>
    </Container>
  )
}

export default InvoicesPage
