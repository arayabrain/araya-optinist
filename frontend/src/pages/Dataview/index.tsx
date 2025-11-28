import { useSelector } from "react-redux"
import { useParams } from "react-router-dom"

import { styled } from "@mui/material"

import DataviewRecords from "components/Dataview/DataviewRecords"
import DataviewWrapper from "components/Dataview/DataviewWrapper"
import { selectCurrentUser } from "store/slice/User/UserSelector"

const Dataview = () => {
  const user = useSelector(selectCurrentUser)
  const { workspaceId } = useParams<{ workspaceId?: string }>()

  return (
    <DataviewWrapper>
      <Title>Dataview</Title>
      <DataviewRecords
        user={user}
        readonly={false}
        metadataEditable={false}
        workspaceId={workspaceId}
      />
    </DataviewWrapper>
  )
}

const Title = styled("h1")(() => ({
  fontSize: 24,
  fontWeight: 600,
  color: "#000000",
  marginBottom: 24,
  marginTop: 0,
}))

export default Dataview
