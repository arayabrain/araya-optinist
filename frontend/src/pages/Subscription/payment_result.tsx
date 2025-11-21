import React from "react"
import { useNavigate } from "react-router-dom"

import CheckIcon from "@mui/icons-material/Check"
import CloseIcon from "@mui/icons-material/Close"
import GitHubIcon from "@mui/icons-material/GitHub"
import { Box, Button, Typography, Card, CardContent } from "@mui/material"

interface PaymentResultProps {
  type?: "success" | "failed"
}

const PaymentResult: React.FC<PaymentResultProps> = ({ type = "success" }) => {
  const navigate = useNavigate()

  const isSuccess = type === "success"

  interface Config {
    icon: React.ReactNode
    circleColor: string
    title: string
    subtitle: string
    description: string
    buttonText: string
    buttonColor: string
    buttonHoverColor: string
    buttonAction: () => void
  }

  const config: Record<"success" | "failed", Config> = {
    success: {
      icon: <CheckIcon sx={{ fontSize: "3rem", color: "white" }} />,
      circleColor: "#10b981",
      title: "Thank you!",
      subtitle: "You are now eligible to use premium services",
      description: "We've sent you an email with the payment information.",
      buttonText: "Dashboard",
      buttonColor: "#3b82f6",
      buttonHoverColor: "#2563eb",
      buttonAction: () => navigate("/dashboard/dashboard"),
    },
    failed: {
      icon: <CloseIcon sx={{ fontSize: "3rem", color: "white" }} />,
      circleColor: "#ef4444",
      title: "Payment Failed",
      subtitle: "We are not able to process your payment",
      description: "Please check your payment information and try again.",
      buttonText: "Try Again",
      buttonColor: "#ef4444",
      buttonHoverColor: "#dc2626",
      buttonAction: () => navigate("/dashboard/subscription"),
    },
  }

  const currentConfig = config[isSuccess ? "success" : "failed"]

  const styles = {
    pageWrapper: {
      minHeight: "70vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: "2rem",
    },
    contentContainer: {
      maxWidth: "64rem",
      width: "100%",
    },
    mainSection: {
      textAlign: "center",
      marginBottom: "3rem",
    },
    iconCircle: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: "5rem",
      height: "5rem",
      backgroundColor: currentConfig.circleColor,
      borderRadius: "50%",
      marginBottom: "1.5rem",
    },
    mainTitle: {
      fontSize: "3rem",
      fontWeight: "bold",
      color: "#111827",
      marginBottom: "1rem",
      "@media (max-width: 768px)": {
        fontSize: "2.5rem",
      },
    },
    subtitlePrimary: {
      fontSize: "1.25rem",
      color: "#4b5563",
      marginBottom: "0.5rem",
    },
    subtitleSecondary: {
      fontSize: "1.125rem",
      color: "#6b7280",
    },
    actionSection: {
      textAlign: "center",
      marginBottom: "4rem",
    },
    mainButton: {
      backgroundColor: currentConfig.buttonColor,
      color: "white",
      fontWeight: "600",
      padding: "0.75rem 2rem",
      borderRadius: "0.5rem",
      textTransform: "none",
      fontSize: "1rem",
      "&:hover": {
        backgroundColor: currentConfig.buttonHoverColor,
      },
    },
    cardsSection: {
      display: "grid",
      gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
      gap: "2rem",
      "@media (min-width: 768px)": {
        gridTemplateColumns: "repeat(2, 1fr)",
      },
    },
    actionCard: {
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
    },
    iconsContainer: {
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      gap: "1rem",
      marginBottom: "1.5rem",
    },
    cardTitle: {
      fontSize: "1.5rem",
      fontWeight: "600",
      color: "#111827",
      marginBottom: "1.5rem",
    },
    documentIconContainer: {
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      marginBottom: "1rem",
    },
    cardDescription: {
      fontSize: "1.125rem",
      color: "#374151",
      marginBottom: "1.5rem",
    },
    visitButton: {
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
    },
    githubButton: {
      backgroundColor: "#374151",
      color: "white",
      fontWeight: "600",
      padding: "0.75rem 2rem",
      borderRadius: "0.5rem",
      textTransform: "none",
      width: "100%",
      maxWidth: "12rem",
      "&:hover": {
        backgroundColor: "#1f2937",
      },
    },
  }

  return (
    <Box sx={styles.pageWrapper}>
      <Box sx={styles.contentContainer}>
        {/* Main Section */}
        <Box sx={styles.mainSection}>
          {/* Icon Circle */}
          <Box sx={styles.iconCircle}>{currentConfig.icon}</Box>

          {/* Main Heading */}
          <Typography variant="h2" sx={styles.mainTitle}>
            {currentConfig.title}
          </Typography>

          {/* Subtitle */}
          <Typography variant="h6" sx={styles.subtitlePrimary}>
            {currentConfig.subtitle}
          </Typography>
          <Typography variant="body1" sx={styles.subtitleSecondary}>
            {currentConfig.description}
          </Typography>
        </Box>

        {/* Main Action Button */}
        <Box sx={styles.actionSection}>
          <Button
            variant="contained"
            sx={styles.mainButton}
            onClick={currentConfig.buttonAction}
          >
            {currentConfig.buttonText}
          </Button>
        </Box>

        {/* Bottom Action Cards */}
        <Box sx={styles.cardsSection}>
          {/* Connect with us Card */}
          <Card sx={styles.actionCard}>
            <CardContent
              sx={{ textAlign: "center", padding: "2rem !important" }}
            >
              <Box sx={styles.iconsContainer}>
                <GitHubIcon sx={{ fontSize: "3rem", color: "#374151" }} />
              </Box>
              <Typography variant="h5" sx={styles.cardTitle}>
                Connect with us
              </Typography>
              <Button
                variant="contained"
                sx={styles.githubButton}
                onClick={() =>
                  window.open(
                    "https://github.com/arayabrain/optinist-for-cloud",
                    "_blank",
                  )
                }
              >
                GitHub
              </Button>
            </CardContent>
          </Card>

          {/* Documentation Card */}
          <Card sx={styles.actionCard}>
            <CardContent
              sx={{ textAlign: "center", padding: "2rem !important" }}
            >
              <Box sx={styles.documentIconContainer}>
                <img
                  src="/static/optinist_logo.png"
                  alt="Optinist Logo"
                  style={{ width: "3rem", height: "3rem" }}
                />
              </Box>
              <Typography variant="body1" sx={styles.cardDescription}>
                Check our documentation
              </Typography>
              <Button
                variant="contained"
                sx={styles.visitButton}
                onClick={() =>
                  window.open(
                    "https://optinist.readthedocs.io/en/latest",
                    "_blank",
                  )
                }
              >
                Visit
              </Button>
            </CardContent>
          </Card>
        </Box>
      </Box>
    </Box>
  )
}

export default PaymentResult
