import { memo, useContext, useEffect } from "react"
import { useSelector, useDispatch } from "react-redux"

import { LinearProgress, Typography } from "@mui/material"

import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import { getHTMLData } from "store/slice/DisplayData/DisplayDataActions"
import {
  selectHTMLData,
  selectHTMLDataError,
  selectHTMLDataIsFulfilled,
  selectHTMLDataIsInitialized,
  selectHTMLDataIsPending,
} from "store/slice/DisplayData/DisplayDataSelectors"
import { AppDispatch } from "store/store"

export const HTMLPlot = memo(function HTMLPlot() {
  const { filePath: path } = useContext(DisplayDataContext)
  const dispatch = useDispatch<AppDispatch>()
  const isPending = useSelector(selectHTMLDataIsPending(path))
  const isInitialized = useSelector(selectHTMLDataIsInitialized(path))
  const error = useSelector(selectHTMLDataError(path))
  const isFulfilled = useSelector(selectHTMLDataIsFulfilled(path))
  useEffect(() => {
    if (!isInitialized) {
      dispatch(getHTMLData({ path }))
    }
  }, [dispatch, isInitialized, path])
  if (isPending) {
    return <LinearProgress />
  } else if (error != null) {
    return <Typography color="error">{error}</Typography>
  } else if (isFulfilled) {
    return <HTMLPlotImple />
  } else {
    return null
  }
})

const HTMLPlotImple = memo(function HTMLPlotImple() {
  const { filePath: path } = useContext(DisplayDataContext)
  const htmlData = useSelector(selectHTMLData(path))

  return (
    // No allow-scripts: the output cannot run code; same-origin lets us size it.
    <iframe
      sandbox="allow-same-origin"
      srcDoc={htmlData}
      title={path}
      onLoad={({ currentTarget: frame }) => {
        const height = frame.contentDocument?.documentElement.scrollHeight
        if (height) frame.style.height = `${height}px`
      }}
      style={{ width: "100%", border: 0 }}
    />
  )
})
