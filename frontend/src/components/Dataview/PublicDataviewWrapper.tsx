import { FC, ReactNode } from "react"

import { Box, styled } from "@mui/material"

import PublicLayout from "components/PublicLayout"

const PublicDataviewWrapper: FC<{ children: ReactNode }> = ({ children }) => {
  return (
    <PublicLayout>
      <Box
        sx={{
          paddingTop: 2,
        }}
      >
        <DataviewContent>{children}</DataviewContent>
      </Box>
    </PublicLayout>
  )
}

const DataviewContent = styled(Box)(() => ({
  width: "94vw",
  margin: "auto",
  marginTop: 15,
}))

export default PublicDataviewWrapper
