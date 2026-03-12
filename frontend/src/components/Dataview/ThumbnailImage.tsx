import { memo, useEffect, useState } from "react"

import CloudDownloadIcon from "@mui/icons-material/CloudDownload"
import ImageIcon from "@mui/icons-material/Image"
import { Box, CircularProgress, IconButton, Tooltip } from "@mui/material"

import { getThumbnailBlobUrl } from "api/outputs/Outputs"

interface ThumbnailImageProps {
  workspaceId: number | string
  uniqueId: string
  thumbType: "input" | "roi"
  onClick?: () => void
  alt?: string
}

/**
 * Authenticated thumbnail image component.
 *
 * Fetches thumbnails using authenticated API calls and displays them.
 * This is necessary because <img src="..."> tags don't send auth headers.
 */
export const ThumbnailImage = memo(function ThumbnailImage({
  workspaceId,
  uniqueId,
  thumbType,
  onClick,
  alt = "Thumbnail",
}: ThumbnailImageProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchThumbnail = async () => {
    setLoading(true)
    setError(null)
    try {
      const url = await getThumbnailBlobUrl(workspaceId, uniqueId, thumbType)
      // Revoke previous blob URL before setting new one to prevent memory leak
      setBlobUrl((prevUrl) => {
        if (prevUrl) {
          URL.revokeObjectURL(prevUrl)
        }
        return url
      })
    } catch (e) {
      const errorMessage =
        e instanceof Error ? e.message : "Failed to load thumbnail"
      setError(errorMessage)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchThumbnail()

    // Cleanup blob URL on unmount
    return () => {
      setBlobUrl((prevUrl) => {
        if (prevUrl) {
          URL.revokeObjectURL(prevUrl)
        }
        return null
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, uniqueId, thumbType])

  if (loading) {
    return (
      <Box
        sx={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <CircularProgress size={20} />
      </Box>
    )
  }

  if (error) {
    return (
      <Box
        sx={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 0.5,
          cursor: "pointer",
        }}
        onClick={onClick}
      >
        <ImageIcon color="disabled" fontSize="large" />
        <Tooltip title="Retry download">
          <IconButton
            size="small"
            onClick={(e) => {
              e.stopPropagation()
              fetchThumbnail()
            }}
            sx={{ padding: 0.25 }}
          >
            <CloudDownloadIcon sx={{ fontSize: 16 }} color="primary" />
          </IconButton>
        </Tooltip>
      </Box>
    )
  }

  return (
    <Box
      onClick={onClick}
      sx={{
        cursor: "pointer",
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <img
        src={blobUrl || ""}
        alt={alt}
        style={{
          maxWidth: "100%",
          maxHeight: "100%",
          objectFit: "contain",
        }}
        loading="lazy"
      />
    </Box>
  )
})
