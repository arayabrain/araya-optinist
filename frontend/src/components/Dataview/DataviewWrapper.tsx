import { FC, ReactNode } from "react"

import { Box, styled } from "@mui/material"

const DataviewWrapper: FC<{ children: ReactNode }> = ({ children }) => {
  return (
    <>
      <Box
        sx={{
          paddingTop: 2,
          paddingBottom: 5,
        }}
      >
        <DataviewContent>{children}</DataviewContent>
      </Box>
    </>
  )
}

const DataviewContent = styled(Box)(() => ({
  width: "94vw",
  margin: "auto",
  marginTop: 15,
}))

export default DataviewWrapper
