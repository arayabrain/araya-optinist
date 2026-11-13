export type TreeNodeType = TreeDirType | TreeFileType

export interface TreeDirType {
  path: string
  name: string
  isDir: true
  nodes: TreeNodeType[]
  dataType?: string | null
}

export interface TreeFileType {
  path: string
  name: string
  isDir: false
  dataType?: string | null
  shape?: number[] | null
  nbytes?: string
}
