import { Box, styled, Typography } from "@mui/material"

interface ValueProp {
  icon: string
  title: string
  description: string
  color: "magenta" | "cyan" | "green" | "yellow"
}

const valueProps: ValueProp[] = [
  {
    icon: "drag_pan",
    title: "No Coding Required",
    description:
      "Build complex analysis pipelines by dragging and dropping. Focus on science, not syntax.",
    color: "magenta",
  },
  {
    icon: "history",
    title: "Reproducible Science",
    description:
      "Every analysis is saved and can be reproduced exactly. Export workflows so reviewers can verify your methods.",
    color: "cyan",
  },
  {
    icon: "share",
    title: "Collaborate Seamlessly",
    description:
      "Share workspaces with colleagues. Publish results publicly so other labs can compare using the same workflows.",
    color: "green",
  },
  {
    icon: "folder_open",
    title: "Multi-Format Support",
    description:
      "Native support for NWB (Neurodata Without Borders), HDF5, MATLAB, CSV, and image formats. Your data, your way.",
    color: "yellow",
  },
]

const iconColors = {
  magenta: { bg: "rgba(225, 29, 72, 0.1)", color: "#e11d48" },
  cyan: { bg: "rgba(13, 148, 136, 0.1)", color: "#0d9488" },
  green: { bg: "rgba(5, 150, 105, 0.1)", color: "#059669" },
  yellow: { bg: "rgba(217, 119, 6, 0.1)", color: "#d97706" },
}

export const ValueProps = () => {
  return (
    <ValuePropsSection>
      <Container>
        <SectionTitle>Why Choose Araya OptiNiSt</SectionTitle>
        <SectionSubtitle>
          Everything you need to go from raw data to publishable insights,
          without writing a single line of code.
        </SectionSubtitle>
        <ValueGrid>
          {valueProps.map((prop, index) => (
            <ValueCard key={index}>
              <ValueIcon
                style={{
                  backgroundColor: iconColors[prop.color].bg,
                  color: iconColors[prop.color].color,
                }}
              >
                <span className="material-symbols-outlined">{prop.icon}</span>
              </ValueIcon>
              <ValueTitle>{prop.title}</ValueTitle>
              <ValueDescription>{prop.description}</ValueDescription>
            </ValueCard>
          ))}
        </ValueGrid>
      </Container>
    </ValuePropsSection>
  )
}

const ValuePropsSection = styled("section")({
  backgroundColor: "white",
  padding: "5rem 0",
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
})

const SectionTitle = styled(Typography)({
  fontSize: "1.875rem",
  fontWeight: 700,
  margin: "0 0 1rem",
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

const ValueGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "1.5rem",
  "@media (min-width: 768px)": {
    gridTemplateColumns: "repeat(2, 1fr)",
  },
  "@media (min-width: 1024px)": {
    gridTemplateColumns: "repeat(4, 1fr)",
  },
})

const ValueCard = styled(Box)({
  padding: "2rem",
  borderRadius: 12,
  border: "1px solid #e5e7eb",
  backgroundColor: "#f9fafb",
  transition: "box-shadow 0.3s",
  "&:hover": {
    boxShadow: "0 20px 40px -12px rgba(0, 0, 0, 0.1)",
  },
})

const ValueIcon = styled(Box)({
  width: 48,
  height: 48,
  borderRadius: 8,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  marginBottom: "1.5rem",
  transition: "transform 0.3s",
  "& .material-symbols-outlined": {
    fontSize: "1.875rem",
  },
})

const ValueTitle = styled(Typography)({
  fontSize: "1.25rem",
  fontWeight: 700,
  margin: "0 0 0.75rem",
})

const ValueDescription = styled(Typography)({
  fontSize: "0.875rem",
  color: "#6b7280",
  lineHeight: 1.6,
  margin: 0,
})
