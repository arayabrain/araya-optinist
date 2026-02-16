import { Box, styled, Typography } from "@mui/material"

interface AudienceCard {
  icon: string
  title: string
  description: string
  features: string[]
  color: "primary" | "cyan" | "green"
}

const audiences: AudienceCard[] = [
  {
    icon: "psychology",
    title: "Neuroscience Labs",
    description:
      "Analyze calcium imaging, electrophysiology, and behavioral data with specialized tools.",
    features: [
      "Calcium imaging pipelines",
      "ROI extraction & analysis",
      "NWB (Neurodata Without Borders) export",
    ],
    color: "primary",
  },
  {
    icon: "biotech",
    title: "Microscopy Researchers",
    description:
      "Build image processing pipelines for various microscopes without coding.",
    features: [
      "Multi-format image support",
      "Batch processing",
      "Spatial filtering tools",
    ],
    color: "cyan",
  },
  {
    icon: "school",
    title: "Educators & Students",
    description:
      "Teach data analysis concepts using our sample data and tools. No coding required for you or your students.",
    features: [
      "Visual learning interface",
      "Shareable workspaces",
      "Focus on science, not syntax",
    ],
    color: "green",
  },
]

const audienceColors = {
  primary: { bg: "rgba(19, 91, 236, 0.1)", color: "#2563eb" },
  cyan: { bg: "rgba(13, 148, 136, 0.1)", color: "#0d9488" },
  green: { bg: "rgba(5, 150, 105, 0.1)", color: "#059669" },
}

export const Audience = () => {
  return (
    <AudienceSection id="audience">
      <Container>
        <SectionHeaderCenter>
          <SectionTitle>Who It&apos;s For</SectionTitle>
          <SectionSubtitle>
            Empowering researchers and educators worldwide.
          </SectionSubtitle>
        </SectionHeaderCenter>
        <AudienceGrid>
          {audiences.map((audience, index) => (
            <AudienceCardWrapper key={index}>
              <AudienceIcon
                style={{
                  backgroundColor: audienceColors[audience.color].bg,
                  color: audienceColors[audience.color].color,
                }}
              >
                <span className="material-symbols-outlined">
                  {audience.icon}
                </span>
              </AudienceIcon>
              <AudienceTitle>{audience.title}</AudienceTitle>
              <AudienceDescription>{audience.description}</AudienceDescription>
              <AudienceFeatures>
                {audience.features.map((feature, featureIndex) => (
                  <AudienceFeature key={featureIndex}>
                    <span
                      className="material-symbols-outlined"
                      style={{ color: audienceColors[audience.color].color }}
                    >
                      check_circle
                    </span>
                    <span>{feature}</span>
                  </AudienceFeature>
                ))}
              </AudienceFeatures>
            </AudienceCardWrapper>
          ))}
        </AudienceGrid>
      </Container>
    </AudienceSection>
  )
}

const AudienceSection = styled("section")({
  backgroundColor: "#f9fafb",
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
  margin: 0,
  maxWidth: 600,
  marginLeft: "auto",
  marginRight: "auto",
})

const AudienceGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "2rem",
  "@media (min-width: 768px)": {
    gridTemplateColumns: "repeat(3, 1fr)",
  },
})

const AudienceCardWrapper = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1.5rem",
  backgroundColor: "white",
  padding: "2.5rem",
  borderRadius: 16,
  border: "1px solid #e5e7eb",
  transition: "box-shadow 0.3s",
  "&:hover": {
    boxShadow: "0 20px 40px -12px rgba(0, 0, 0, 0.1)",
  },
})

const AudienceIcon = styled(Box)({
  width: 56,
  height: 56,
  borderRadius: "50%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  "& .material-symbols-outlined": {
    fontSize: "1.875rem",
  },
})

const AudienceTitle = styled(Typography)({
  fontSize: "1.5rem",
  fontWeight: 700,
  margin: 0,
})

const AudienceDescription = styled(Typography)({
  fontSize: "0.875rem",
  color: "#6b7280",
  margin: 0,
  lineHeight: 1.6,
})

const AudienceFeatures = styled("ul")({
  listStyle: "none",
  padding: 0,
  margin: "auto 0 0",
  display: "flex",
  flexDirection: "column",
  gap: "0.75rem",
})

const AudienceFeature = styled("li")({
  display: "flex",
  alignItems: "center",
  gap: "0.75rem",
  fontSize: "0.875rem",
  "& .material-symbols-outlined": {
    fontSize: "1.125rem",
  },
})
