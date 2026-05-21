import { FC } from "react"

import { Box, Container, Divider, Grid, Typography } from "@mui/material"

const PublicFooter: FC = () => {
  return (
    <>
      <Box sx={{ paddingTop: 4, paddingBottom: 2 }}>
        <Divider />
      </Box>
      <Box>
        <Container>
          <Grid container direction="column" alignItems="center">
            <Grid item xs={12}>
              <Typography>
                {`${new Date().getFullYear()}`} Studio. All rights reserved.
              </Typography>
            </Grid>
          </Grid>
        </Container>
      </Box>
    </>
  )
}

export default PublicFooter
