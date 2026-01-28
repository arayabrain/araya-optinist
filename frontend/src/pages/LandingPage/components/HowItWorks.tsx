import { Box, styled, Typography } from "@mui/material"

interface Step {
  number: string
  title: string
  description: string
  color: "primary" | "cyan" | "green"
}

const steps: Step[] = [
  {
    number: "1",
    title: "Upload Your Data",
    description:
      "Import images, HDF5, MATLAB, CSV, or NWB files. Organize in workspaces.",
    color: "primary",
  },
  {
    number: "2",
    title: "Build Your Pipeline",
    description:
      "Drag algorithms onto the blank workflow, connect nodes, configure parameters visually.",
    color: "cyan",
  },
  {
    number: "3",
    title: "Analyze & Share",
    description:
      "Run pipelines, visualize results, export figures, and share with your team.",
    color: "green",
  },
]

const stepColors = {
  primary: { bg: "rgba(19, 91, 236, 0.1)", color: "#2563eb" },
  cyan: { bg: "rgba(13, 148, 136, 0.1)", color: "#0d9488" },
  green: { bg: "rgba(5, 150, 105, 0.1)", color: "#059669" },
}

export const HowItWorks = () => {
  return (
    <HowItWorksSection>
      <Container>
        <SectionHeaderCenter>
          <Label>Simple Workflow</Label>
          <SectionTitle>From Data to Insights in 3 Steps</SectionTitle>
          <SectionSubtitle>
            No complex setup. No coding. Just results.
          </SectionSubtitle>
        </SectionHeaderCenter>
        <StepsGrid>
          {steps.map((step, index) => (
            <StepItem key={index}>
              <StepNumber
                style={{
                  backgroundColor: stepColors[step.color].bg,
                  color: stepColors[step.color].color,
                }}
              >
                <span>{step.number}</span>
              </StepNumber>
              <StepTitle>{step.title}</StepTitle>
              <StepDescription>{step.description}</StepDescription>
            </StepItem>
          ))}
        </StepsGrid>
      </Container>
    </HowItWorksSection>
  )
}

const HowItWorksSection = styled("section")({
  backgroundColor: "white",
  padding: "5rem 0",
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
})

const SectionHeaderCenter = styled(Box)({
  textAlign: "center",
  marginBottom: "4rem",
})

const Label = styled(Typography)({
  color: "#2563eb",
  fontWeight: 700,
  letterSpacing: "0.1em",
  fontSize: "0.75rem",
  textTransform: "uppercase",
})

const SectionTitle = styled(Typography)({
  fontSize: "1.875rem",
  fontWeight: 700,
  margin: "1rem 0 1rem",
  textAlign: "center",
})

const SectionSubtitle = styled(Typography)({
  textAlign: "center",
  color: "#6b7280",
  margin: "0 0 3rem",
  maxWidth: 600,
  marginLeft: "auto",
  marginRight: "auto",
})

const StepsGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "2rem",
  "@media (min-width: 768px)": {
    gridTemplateColumns: "repeat(3, 1fr)",
  },
})

const StepItem = styled(Box)({
  textAlign: "center",
})

const StepNumber = styled(Box)({
  width: 64,
  height: 64,
  borderRadius: 16,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  margin: "0 auto 1.5rem",
  "& span": {
    fontSize: "1.875rem",
    fontWeight: 900,
  },
})

const StepTitle = styled(Typography)({
  fontSize: "1.25rem",
  fontWeight: 700,
  margin: "0 0 0.75rem",
})

const StepDescription = styled(Typography)({
  fontSize: "0.875rem",
  color: "#6b7280",
  margin: 0,
})
