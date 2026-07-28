import { FC, ReactNode } from "react"

import { Typography, styled } from "@mui/material"

export const P: FC<{ children: ReactNode }> = ({ children }) => (
  <Typography sx={{ mb: 2 }}>{children}</Typography>
)

export const H2: FC<{ children: ReactNode }> = ({ children }) => (
  <Typography variant="h6" sx={{ mt: 4, mb: 1.5, fontWeight: 700 }}>
    {children}
  </Typography>
)

export const H3: FC<{ children: ReactNode }> = ({ children }) => (
  <Typography variant="subtitle1" sx={{ mt: 2, mb: 1, fontWeight: 700 }}>
    {children}
  </Typography>
)

export const ReviewFlag = styled("span")({
  color: "#c62828",
  fontWeight: 600,
})

const StyledAnchor = styled("a")({
  color: "inherit",
  textDecorationColor: "inherit",
})

export const ExternalLink: FC<{ href: string; children: ReactNode }> = ({
  href,
  children,
}) => (
  <StyledAnchor href={href} target="_blank" rel="noopener noreferrer">
    {children}
  </StyledAnchor>
)
