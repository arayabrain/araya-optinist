import { useSelector } from "react-redux"

import { styled } from "@mui/material"

import DataviewRecords from "components/Dataview/DataviewRecords"
import DataviewWrapper from "components/Dataview/DataviewWrapper"
import { selectCurrentUser } from "store/slice/User/UserSelector"

const Dataview = () => {
  const user = useSelector(selectCurrentUser)
  return (
    <DataviewWrapper>
      <Title>Dataview</Title>
      <DataviewRecords
        user={user}
        cellPath="/console/cells"
        readonly={false}
        metadataEditable={false}
      />
    </DataviewWrapper>
  )
}

const Title = styled("h1")(() => ({}))

export default Dataview
