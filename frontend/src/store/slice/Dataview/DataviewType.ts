export const DATAVIEW_SLICE_NAME = "dataview"

export type OrderBy = "ASC" | "DESC" | ""

export type DataviewType = {
  id: number
  uid: string
  name: string
  owner: {
    name?: string
  }
  workspace: {
    id: number
    name?: string
  }
  thumbnails: {
    image_url?: string
    roi_url?: string
  }
  attributes?: object
  analyzed_at: string
  publish_status?: number
  created_at: string
  updated_at: string
}

export type DataviewDTO = {
  offset: number
  limit: number
  total: number
  items: DataviewType[]
}

export type DataviewParams = {
  [key: string]: number | string | string[] | undefined
}
