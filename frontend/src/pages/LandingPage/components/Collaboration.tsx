import { Box, styled, Typography } from "@mui/material"

interface CollabFeature {
  icon: string
  title: string
  description: string
  color: "primary" | "cyan" | "green"
}

const collabFeatures: CollabFeature[] = [
  {
    icon: "group_add",
    title: "Team Workspaces",
    description:
      "Invite colleagues to shared workspaces. Everyone stays in sync.",
    color: "primary",
  },
  {
    icon: "history",
    title: "Version Control",
    description:
      "Track changes to workflows and experiments. Roll back anytime.",
    color: "cyan",
  },
  {
    icon: "download",
    title: "Export Anywhere",
    description:
      "Download workflows, export to NWB, or generate Snakemake pipelines.",
    color: "green",
  },
]

const collabColors = {
  primary: { bg: "rgba(19, 91, 236, 0.1)", color: "#2563eb" },
  cyan: { bg: "rgba(13, 148, 136, 0.1)", color: "#0d9488" },
  green: { bg: "rgba(5, 150, 105, 0.1)", color: "#059669" },
}

export const Collaboration = () => {
  return (
    <CollaborationSection>
      <Container>
        <CollabGrid>
          <CollabContent>
            <Label>Collaboration</Label>
            <SectionTitleLeft>Work Together, Discover Faster</SectionTitleLeft>
            <CollabDescription>
              Share workspaces with your team, publish results for peer review,
              and ensure every experiment is reproducible.
            </CollabDescription>
            <CollabFeatures>
              {collabFeatures.map((feature, index) => (
                <CollabFeature key={index}>
                  <CollabIcon
                    style={{
                      backgroundColor: collabColors[feature.color].bg,
                      color: collabColors[feature.color].color,
                    }}
                  >
                    <span className="material-symbols-outlined">
                      {feature.icon}
                    </span>
                  </CollabIcon>
                  <div>
                    <CollabFeatureTitle>{feature.title}</CollabFeatureTitle>
                    <CollabFeatureDescription>
                      {feature.description}
                    </CollabFeatureDescription>
                  </div>
                </CollabFeature>
              ))}
            </CollabFeatures>
          </CollabContent>
          <CollabVisual>
            <CollabGlow />
            <CollabCard>
              <CollabAvatars>
                <Avatar style={{ backgroundColor: "#2563eb" }}>JD</Avatar>
                <Avatar style={{ backgroundColor: "#0d9488", marginLeft: -16 }}>
                  MK
                </Avatar>
                <Avatar style={{ backgroundColor: "#059669", marginLeft: -16 }}>
                  AS
                </Avatar>
                <Avatar
                  style={{
                    backgroundColor: "#d1d5db",
                    color: "#6b7280",
                    marginLeft: -16,
                  }}
                >
                  +5
                </Avatar>
              </CollabAvatars>
              <CollabPlaceholder>
                <PlaceholderLine style={{ width: "75%" }} />
                <PlaceholderLine style={{ width: "50%" }} />
                <PlaceholderLine style={{ width: "66%" }} />
              </CollabPlaceholder>
              <CollabFooter>
                <CollabFooterText>Shared Workspace</CollabFooterText>
                <CollabBadge>8 members</CollabBadge>
              </CollabFooter>
            </CollabCard>
          </CollabVisual>
        </CollabGrid>
      </Container>
    </CollaborationSection>
  )
}

const CollaborationSection = styled("section")({
  backgroundColor: "white",
  padding: "5rem 0",
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
})

const CollabGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "3rem",
  alignItems: "center",
  "@media (min-width: 1024px)": {
    gridTemplateColumns: "1fr 1fr",
  },
})

const CollabContent = styled(Box)({
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

const SectionTitleLeft = styled(Typography)({
  fontSize: "2.5rem",
  fontWeight: 900,
  margin: 0,
})

const CollabDescription = styled(Typography)({
  color: "#6b7280",
  margin: "0 0 1rem",
  lineHeight: 1.6,
})

const CollabFeatures = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
})

const CollabFeature = styled(Box)({
  display: "flex",
  alignItems: "flex-start",
  gap: "1rem",
})

const CollabIcon = styled(Box)({
  width: 40,
  height: 40,
  borderRadius: 8,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
})

const CollabFeatureTitle = styled(Typography)({
  fontWeight: 700,
  margin: "0 0 0.25rem",
})

const CollabFeatureDescription = styled(Typography)({
  fontSize: "0.875rem",
  color: "#6b7280",
  margin: 0,
})

const CollabVisual = styled(Box)({
  position: "relative",
})

const CollabGlow = styled(Box)({
  position: "absolute",
  inset: "-1rem",
  background: "linear-gradient(to right, #059669, #0d9488, #2563eb)",
  opacity: 0.1,
  filter: "blur(40px)",
  borderRadius: 20,
})

const CollabCard = styled(Box)({
  position: "relative",
  backgroundColor: "#f9fafb",
  borderRadius: 16,
  padding: "2rem",
  border: "1px solid #e5e7eb",
})

const CollabAvatars = styled(Box)({
  display: "flex",
  alignItems: "center",
  marginBottom: "1.5rem",
})

const Avatar = styled(Box)({
  width: 48,
  height: 48,
  borderRadius: "50%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  fontWeight: 700,
  color: "white",
  border: "2px solid white",
})

const CollabPlaceholder = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "0.75rem",
})

const PlaceholderLine = styled(Box)({
  height: 12,
  backgroundColor: "#e5e7eb",
  borderRadius: 4,
})

const CollabFooter = styled(Box)({
  marginTop: "1.5rem",
  paddingTop: "1.5rem",
  borderTop: "1px solid #e5e7eb",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
})

const CollabFooterText = styled(Typography)({
  fontSize: "0.875rem",
  color: "#6b7280",
})

const CollabBadge = styled("span")({
  fontSize: "0.75rem",
  fontWeight: 700,
  backgroundColor: "rgba(5, 150, 105, 0.2)",
  color: "#059669",
  padding: "0.25rem 0.75rem",
  borderRadius: 9999,
})
