import { useMemo } from "react"
import { ThemeProvider, useMediaQuery } from "@mui/material"
import createAppTheme from "./theme"

export default function ThemeWrapper({ children }) {
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)")
  const theme = useMemo(() => createAppTheme(prefersDark ? "dark" : "light"), [prefersDark])

  return <ThemeProvider theme={theme}>{children}</ThemeProvider>
}
