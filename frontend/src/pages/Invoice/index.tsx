import { useNavigate } from "react-router-dom"

import ArrowBackIcon from "@mui/icons-material/ArrowBack"
import { Box, Typography, Button, IconButton } from "@mui/material"
import { styled } from "@mui/material/styles"

// Styled Components
const Container = styled(Box)(() => ({
  minHeight: "100vh",
  padding: "24px",
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

const Logo = styled(Box)(() => ({
  width: "64px",
  height: "64px",
  background: "black",
  borderRadius: "50%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  position: "relative",
}))

const LogoDollar = styled(Typography)(() => ({
  color: "#ef4444",
  fontSize: "24px",
  fontWeight: "bold",
}))

const LogoDot1 = styled(Box)(() => ({
  position: "absolute",
  top: "-4px",
  right: "-4px",
  width: "16px",
  height: "16px",
  background: "#ef4444",
  borderRadius: "50%",
}))

const LogoDot2 = styled(Box)(() => ({
  position: "absolute",
  bottom: "-4px",
  left: "-4px",
  width: "12px",
  height: "12px",
  background: "#ef4444",
  borderRadius: "50%",
}))

const BrandText = styled(Typography)(() => ({
  color: "#16a34a",
  fontSize: "36px",
  fontWeight: "bold",
  fontFamily: "Arial, sans-serif",
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
}))

const PaymentTitle = styled(Typography)(() => ({
  fontSize: "20px",
  fontWeight: "bold",
  color: "#111827",
  marginBottom: "16px",
}))

const VisaIcon = styled(Box)(() => ({
  width: "32px",
  height: "24px",
  background: "#1e40af",
  borderRadius: "4px",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  marginRight: "12px",
}))

const VisaText = styled(Typography)(() => ({
  color: "white",
  fontSize: "12px",
  fontWeight: "bold",
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
}))

const TableHeader = styled("thead")(() => ({
  background: "#f9fafb",
}))

const TableHeaderCell = styled("th")(() => ({
  padding: "16px 24px",
  textAlign: "left",
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
  "&:first-of-type td": {
    borderBottom: "1px solid #e5e7eb",
  },
}))

const BackButton = styled(IconButton)(() => ({
  position: "absolute",
  top: "80px",
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
}))

const InvoicesPage = () => {
  const navigate = useNavigate()

  const handleGoBack = () => {
    // You can replace this with your navigation logic
    window.history.back()
    // Or use React Router: navigate(-1);
  }

  const handleAdjustPlan = () => {
    navigate("/console/subscription")
  }

  return (
    <Container>
      {/* Go Back Button */}
      <BackButton onClick={handleGoBack} aria-label="Go back">
        <ArrowBackIcon />
      </BackButton>

      <MainWrapper>
        <MainContainer>
          {/* Premium Plan Section */}
          <Section>
            <FlexContainer>
              <FlexRow>
                {/* Logo */}
                <Logo>
                  <LogoDollar>$</LogoDollar>
                  <LogoDot1 />
                  <LogoDot2 />
                </Logo>

                <BrandText>OPTINIST</BrandText>

                <Box>
                  <PlanTitle>Premium Plan</PlanTitle>
                  <PlanType>Monthly</PlanType>
                  <ExpirationText>
                    You subscription will expire on 2025/08/25
                  </ExpirationText>
                </Box>
              </FlexRow>

              <PrimaryButton onClick={handleAdjustPlan}>
                Adjust Plan
              </PrimaryButton>
            </FlexContainer>
          </Section>

          {/* Payment Section */}
          <Section>
            <FlexContainer>
              <Box>
                <PaymentTitle>Payment</PaymentTitle>
                <FlexRow>
                  <VisaIcon>
                    <VisaText>V</VisaText>
                  </VisaIcon>
                  <CardNumber>Visa ******1999</CardNumber>
                </FlexRow>
              </Box>

              <PrimaryButton>Update</PrimaryButton>
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
                  <TableRow>
                    <TableCell>July 25 2025</TableCell>
                    <TableCell>$20</TableCell>
                    <TableCell>Paid</TableCell>
                    <TableCell>
                      <ViewButton>View</ViewButton>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>June 25 2025</TableCell>
                    <TableCell>$20</TableCell>
                    <TableCell>Paid</TableCell>
                    <TableCell>
                      <ViewButton>View</ViewButton>
                    </TableCell>
                  </TableRow>
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
