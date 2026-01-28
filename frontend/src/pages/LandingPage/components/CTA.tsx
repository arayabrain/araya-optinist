import { Box, styled, Typography } from "@mui/material"

export const CTA = () => {
  return (
    <CTASection>
      <Container>
        <CTAWrapper>
          <CTAGlow1 />
          <CTAGlow2 />
          <CTATitle>Ready to Transform Your Research?</CTATitle>
          <CTADescription>
            Join researchers worldwide using OptiNiSt to accelerate their
            scientific discoveries.
          </CTADescription>
          <CTAButtons>
            <CTAPrimaryButton>Start Free Trial</CTAPrimaryButton>
            <CTASecondaryButton>View Documentation</CTASecondaryButton>
          </CTAButtons>
        </CTAWrapper>
      </Container>
    </CTASection>
  )
}

const CTASection = styled("section")({
  padding: "5rem 0",
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
})

const CTAWrapper = styled(Box)({
  backgroundColor: "#2563eb",
  borderRadius: 24,
  padding: "3rem",
  textAlign: "center",
  color: "white",
  position: "relative",
  overflow: "hidden",
})

const CTAGlow1 = styled(Box)({
  position: "absolute",
  width: 256,
  height: 256,
  borderRadius: "50%",
  filter: "blur(60px)",
  top: 0,
  right: 0,
  backgroundColor: "rgba(255, 255, 255, 0.1)",
  transform: "translate(30%, -30%)",
})

const CTAGlow2 = styled(Box)({
  position: "absolute",
  width: 256,
  height: 256,
  borderRadius: "50%",
  filter: "blur(60px)",
  bottom: 0,
  left: 0,
  backgroundColor: "rgba(13, 148, 136, 0.2)",
  transform: "translate(-30%, 30%)",
})

const CTATitle = styled(Typography)({
  fontSize: "2.5rem",
  fontWeight: 900,
  margin: "0 0 1.5rem",
  position: "relative",
  zIndex: 10,
})

const CTADescription = styled(Typography)({
  color: "rgba(255, 255, 255, 0.8)",
  maxWidth: 600,
  margin: "0 auto 2.5rem",
  fontSize: "1.125rem",
  position: "relative",
  zIndex: 10,
})

const CTAButtons = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  justifyContent: "center",
  position: "relative",
  zIndex: 10,
  "@media (min-width: 640px)": {
    flexDirection: "row",
  },
})

const CTAPrimaryButton = styled("button")({
  backgroundColor: "white",
  color: "#2563eb",
  fontWeight: 900,
  height: 56,
  padding: "0 2.5rem",
  borderRadius: 12,
  border: "none",
  cursor: "pointer",
  transition: "background-color 0.2s",
  "&:hover": {
    backgroundColor: "#f3f4f6",
  },
})

const CTASecondaryButton = styled("button")({
  backgroundColor: "rgba(19, 91, 236, 0.5)",
  backdropFilter: "blur(8px)",
  border: "1px solid rgba(255, 255, 255, 0.3)",
  color: "white",
  fontWeight: 900,
  height: 56,
  padding: "0 2.5rem",
  borderRadius: 12,
  cursor: "pointer",
  transition: "background-color 0.2s",
  "&:hover": {
    backgroundColor: "rgba(19, 91, 236, 0.6)",
  },
})
