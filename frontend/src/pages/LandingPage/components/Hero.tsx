import { useNavigate } from "react-router-dom"

import { Box, styled, Typography } from "@mui/material"

export const Hero = () => {
  const navigate = useNavigate()

  return (
    <HeroSection>
      <HeroGrid>
        <HeroContent>
          <HeroText>
            <Label>Visual Data Analysis for Science</Label>
            <HeroTitle>
              Build. Analyze. <GradientText>Collaborate.</GradientText>
            </HeroTitle>
            <HeroSubtitle>
              with <BrandText>OptiNiSt</BrandText>
            </HeroSubtitle>
            <HeroDescription>
              The no-code platform for scientific data analysis.
              <br />
              Build pipelines visually, ensure reproducibility, and collaborate
              seamlessly.
            </HeroDescription>
          </HeroText>
          <HeroButtons>
            <PrimaryButtonLg onClick={() => navigate("/login")}>
              Get Started
            </PrimaryButtonLg>
          </HeroButtons>
          <HeroBadges>
            <Badge>
              <span
                className="material-symbols-outlined"
                style={{ color: "#059669" }}
              >
                check_circle
              </span>
              <span>No coding required</span>
            </Badge>
            <Badge>
              <span
                className="material-symbols-outlined"
                style={{ color: "#059669" }}
              >
                check_circle
              </span>
              <span>NWB (Neurodata Without Borders) compatible</span>
            </Badge>
          </HeroBadges>
        </HeroContent>
        <HeroVisual>
          <HeroImage src="/static/optinist_logo.png" alt="OptiNiSt logo" />
        </HeroVisual>
      </HeroGrid>
    </HeroSection>
  )
}

const HeroSection = styled("section")({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "4rem 1.5rem",
  "@media (min-width: 768px)": {
    padding: "6rem 1.5rem",
  },
})

const HeroGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "3rem",
  alignItems: "center",
  "@media (min-width: 1024px)": {
    gridTemplateColumns: "1fr 1fr",
  },
})

const HeroContent = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "2rem",
})

const HeroText = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
})

const Label = styled(Typography)({
  color: "#2563eb",
  fontWeight: 700,
  letterSpacing: "0.1em",
  fontSize: "0.75rem",
  textTransform: "uppercase",
})

const HeroTitle = styled("h1")({
  fontSize: "3rem",
  fontWeight: 900,
  lineHeight: 1.1,
  letterSpacing: "-0.03em",
  margin: 0,
  "@media (min-width: 768px)": {
    fontSize: "3.75rem",
  },
})

const GradientText = styled("span")({
  background: "linear-gradient(135deg, #2563eb 0%, #0d9488 50%, #059669 100%)",
  WebkitBackgroundClip: "text",
  WebkitTextFillColor: "transparent",
  backgroundClip: "text",
})

const HeroSubtitle = styled("div")({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  fontSize: "1.5rem",
  fontWeight: 500,
  color: "#4b5563",
  marginTop: "0.5rem",
  "@media (min-width: 768px)": {
    fontSize: "2rem",
  },
})

const BrandText = styled("span")({
  background: "linear-gradient(135deg, #0d9488 0%, #059669 100%)",
  WebkitBackgroundClip: "text",
  WebkitTextFillColor: "transparent",
  backgroundClip: "text",
  fontWeight: 800,
  letterSpacing: "-0.02em",
})

const HeroDescription = styled(Typography)({
  fontSize: "1.125rem",
  color: "#6b7280",
  maxWidth: 500,
  margin: 0,
  lineHeight: 1.6,
})

const HeroButtons = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  "@media (min-width: 640px)": {
    flexDirection: "row",
  },
})

const PrimaryButtonLg = styled("button")({
  backgroundColor: "#2563eb",
  color: "white",
  fontWeight: 700,
  height: 48,
  padding: "0 2rem",
  fontSize: "1rem",
  borderRadius: 8,
  border: "none",
  cursor: "pointer",
  transition: "background-color 0.2s",
  "&:hover": {
    backgroundColor: "#1d4ed8",
  },
})

const HeroBadges = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "1.5rem",
  paddingTop: "1rem",
})

const Badge = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  fontSize: "0.875rem",
  color: "#6b7280",
})

const HeroVisual = styled(Box)({
  position: "relative",
  display: "flex",
  justifyContent: "center",
  alignItems: "center",
  "@media (min-width: 1024px)": {
    transform: "scale(1.15)",
    transformOrigin: "center center",
  },
})

const HeroImage = styled("img")({
  width: "100%",
  maxWidth: 600,
  height: "auto",
})
