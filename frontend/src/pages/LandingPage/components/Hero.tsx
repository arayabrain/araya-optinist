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
          <HeroGlow />
          <HeroCard>
            <HeroCardHeader>
              <Dot style={{ backgroundColor: "#f87171" }} />
              <Dot style={{ backgroundColor: "#fbbf24" }} />
              <Dot style={{ backgroundColor: "#4ade80" }} />
            </HeroCardHeader>
            <WorkflowPreview>
              <WorkflowNode
                style={{
                  backgroundColor: "rgba(13, 148, 136, 0.15)",
                  border: "2px solid #0d9488",
                  color: "#0d9488",
                }}
              >
                <span>Image Input</span>
              </WorkflowNode>
              <WorkflowConnector />
              <WorkflowNode
                style={{
                  backgroundColor: "rgba(37, 99, 235, 0.15)",
                  border: "2px solid #2563eb",
                  color: "#2563eb",
                }}
              >
                <span>Algorithm</span>
              </WorkflowNode>
              <WorkflowConnector />
              <WorkflowNode
                style={{
                  backgroundColor: "rgba(5, 150, 105, 0.15)",
                  border: "2px solid #059669",
                  color: "#059669",
                }}
              >
                <span>Visualize</span>
              </WorkflowNode>
            </WorkflowPreview>
          </HeroCard>
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

const Attribution = styled(Typography)({
  fontSize: "0.8rem",
  color: "#9ca3af",
  fontStyle: "italic",
  marginTop: "0.5rem",
})

const HeroVisual = styled(Box)({
  position: "relative",
})

const HeroGlow = styled(Box)({
  position: "absolute",
  inset: "-1rem",
  background: "linear-gradient(to right, #0d9488, #2563eb, #e11d48)",
  opacity: 0.1,
  filter: "blur(40px)",
  borderRadius: 20,
  transition: "opacity 0.5s",
})

const HeroCard = styled(Box)({
  position: "relative",
  width: "100%",
  aspectRatio: "16 / 9",
  background: "linear-gradient(to bottom right, #f9fafb, #f3f4f6)",
  border: "1px solid #e5e7eb",
  borderRadius: 12,
  overflow: "hidden",
  boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.15)",
  padding: "1.5rem",
})

const HeroCardHeader = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  marginBottom: "1rem",
})

const Dot = styled(Box)({
  width: 12,
  height: 12,
  borderRadius: "50%",
})

const WorkflowPreview = styled(Box)({
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "1rem",
  height: "calc(100% - 2rem)",
})

const WorkflowNode = styled(Box)({
  padding: "0.75rem 1.5rem",
  borderRadius: 8,
  fontSize: "0.75rem",
  fontWeight: 700,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
})

const WorkflowConnector = styled(Box)({
  width: 32,
  height: 2,
  backgroundColor: "#d1d5db",
})
