import { FC, ReactNode } from "react"

import { Box, styled, Typography } from "@mui/material"

import PublicHeader from "components/PublicLayout/PublicHeader"

type LegalPageLayoutProps = {
  title: string
  children: ReactNode
}

// ponytail: shared shell for Terms/Privacy - both are static text pages with
// content pending, add real styling once actual copy comes in.
const LegalPageLayout: FC<LegalPageLayoutProps> = ({ title, children }) => {
  return (
    <>
      <PublicHeader />
      <Wrapper>
        <Typography variant="h4" component="h1" sx={{ mb: 3 }}>
          {title}
        </Typography>
        {children}
      </Wrapper>
    </>
  )
}

const Wrapper = styled(Box)({
  maxWidth: 800,
  margin: "0 auto",
  padding: "96px 24px 48px",
})

export default LegalPageLayout
