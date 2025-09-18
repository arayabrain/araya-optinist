import React from "react"
import { useNavigate } from "react-router-dom"

import CloseIcon from "@mui/icons-material/Close"
import GitHubIcon from "@mui/icons-material/GitHub"
import {
  Box,
  Button,
  Typography,
  styled,
  Card,
  CardContent,
} from "@mui/material"

const Failed = () => {
  const navigate = useNavigate()
  return (
    <PageWrapper>
      <ContentContainer>
        {/* Main Failed Section */}
        <MainSection>
          {/* Red X Circle */}
          <ErrorCircle>
            <CloseIcon sx={{ fontSize: "3rem", color: "white" }} />
          </ErrorCircle>

          {/* Main Heading */}
          <MainTitle variant="h2">Payment Failed</MainTitle>

          {/* Subtitle */}
          <SubtitlePrimary variant="h6">
            We are not able to process your payment
          </SubtitlePrimary>
          <SubtitleSecondary variant="body1">
            Please check your payment information and try again.
          </SubtitleSecondary>
        </MainSection>

        {/* Try Again Button */}
        <RetrySection>
          <RetryButton
            variant="contained"
            onClick={() => navigate("/console/subscription")}
          >
            Try Again
          </RetryButton>
        </RetrySection>

        {/* Bottom Action Cards */}
        <CardsSection>
          {/* Connect with us Card */}
          <ActionCard>
            <CardContent
              sx={{ textAlign: "center", padding: "2rem !important" }}
            >
              <IconsContainer>
                <GitHubIcon
                  sx={{ fontSize: "3rem", color: "#374151", cursor: "pointer" }}
                  onClick={() =>
                    window.open(
                      "https://github.com/arayabrain/optinist-for-cloud",
                      "_blank",
                    )
                  }
                />
              </IconsContainer>
              <CardTitle variant="h5">Connect with us</CardTitle>
            </CardContent>
          </ActionCard>

          {/* Documentation Card */}
          <ActionCard>
            <CardContent
              sx={{ textAlign: "center", padding: "2rem !important" }}
            >
              <DocumentIconContainer>
                <img
                  src="/static/optinist_logo.png"
                  alt="Optinist Logo"
                  style={{ width: "3rem", height: "3rem" }}
                />
              </DocumentIconContainer>
              <CardDescription variant="body1">
                Check our documentation
              </CardDescription>
              <VisitButton
                variant="contained"
                onClick={() =>
                  window.open(
                    "https://optinist.readthedocs.io/en/latest",
                    "_blank",
                  )
                }
              >
                Visit
              </VisitButton>
            </CardContent>
          </ActionCard>
        </CardsSection>
      </ContentContainer>
    </PageWrapper>
  )
}

// Styled Components
const PageWrapper = styled(Box)(() => ({
  minHeight: "70vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "2rem",
}))

const ContentContainer = styled(Box)(() => ({
  maxWidth: "64rem",
  width: "100%",
}))

const MainSection = styled(Box)(() => ({
  textAlign: "center",
  marginBottom: "3rem",
}))

const ErrorCircle = styled(Box)(() => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: "5rem",
  height: "5rem",
  backgroundColor: "#ef4444",
  borderRadius: "50%",
  marginBottom: "1.5rem",
}))

const MainTitle = styled(Typography)(() => ({
  fontSize: "3rem",
  fontWeight: "bold",
  color: "#111827",
  marginBottom: "1rem",
  "@media (max-width: 768px)": {
    fontSize: "2.5rem",
  },
}))

const SubtitlePrimary = styled(Typography)(() => ({
  fontSize: "1.25rem",
  color: "#4b5563",
  marginBottom: "0.5rem",
}))

const SubtitleSecondary = styled(Typography)(() => ({
  fontSize: "1.125rem",
  color: "#6b7280",
}))

const RetrySection = styled(Box)(() => ({
  textAlign: "center",
  marginBottom: "4rem",
}))

const RetryButton = styled(Button)(() => ({
  backgroundColor: "#ef4444",
  color: "white",
  fontWeight: "600",
  padding: "0.75rem 2rem",
  borderRadius: "0.5rem",
  textTransform: "none",
  fontSize: "1rem",
  "&:hover": {
    backgroundColor: "#dc2626",
  },
}))

const CardsSection = styled(Box)(() => ({
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
  gap: "2rem",
  "@media (min-width: 768px)": {
    gridTemplateColumns: "repeat(2, 1fr)",
  },
}))

const ActionCard = styled(Card)(() => ({
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  border: "2px solid #dbeafe",
  borderRadius: "0.5rem",
  backgroundColor: "white",
  boxShadow: "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
  transition: "transform 0.2s ease-in-out, box-shadow 0.2s ease-in-out",
  "&:hover": {
    transform: "translateY(-2px)",
    boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
  },
}))

const IconsContainer = styled(Box)(() => ({
  display: "flex",
  justifyContent: "center",
  alignItems: "center",
  gap: "1rem",
  marginBottom: "1.5rem",
}))

const CardTitle = styled(Typography)(() => ({
  fontSize: "1.5rem",
  fontWeight: "600",
  color: "#111827",
}))

const DocumentIconContainer = styled(Box)(() => ({
  display: "flex",
  justifyContent: "center",
  alignItems: "center",
  marginBottom: "1rem",
}))

const CardDescription = styled(Typography)(() => ({
  fontSize: "1.125rem",
  color: "#374151",
  marginBottom: "1.5rem",
}))

const VisitButton = styled(Button)(() => ({
  backgroundColor: "#3b82f6",
  color: "white",
  fontWeight: "600",
  padding: "0.75rem 2rem",
  borderRadius: "0.5rem",
  textTransform: "none",
  width: "100%",
  maxWidth: "12rem",
  "&:hover": {
    backgroundColor: "#2563eb",
  },
}))

export default Failed
