import { Component } from "react"
import { Box, Typography, Button, Paper } from "@mui/material"
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline"

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo)
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null })
    window.location.reload()
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <Box sx={{
          display: "flex", alignItems: "center", justifyContent: "center",
          height: "100vh", p: 3, bgcolor: "background.default",
        }}>
          <Paper variant="outlined" sx={{ p: 4, maxWidth: 480, textAlign: "center", borderRadius: 3 }}>
            <ErrorOutlineIcon sx={{ fontSize: 56, color: "error.main", mb: 2 }} />
            <Typography variant="h5" sx={{ fontWeight: 700, mb: 1 }}>
              Something went wrong
            </Typography>
            <Typography variant="body2" sx={{ color: "text.secondary", mb: 3 }}>
              An unexpected error occurred. Your data is safe — try reloading the page.
            </Typography>
            {this.state.error && (
              <Paper variant="outlined" sx={{
                p: 1.5, mb: 3, bgcolor: "action.hover", borderRadius: 2,
                maxHeight: 100, overflow: "auto", textAlign: "left",
              }}>
                <Typography variant="caption" sx={{ fontFamily: "monospace", fontSize: "0.7rem", color: "error.main" }}>
                  {this.state.error.message || String(this.state.error)}
                </Typography>
              </Paper>
            )}
            <Box sx={{ display: "flex", gap: 1.5, justifyContent: "center" }}>
              <Button variant="outlined" onClick={this.handleReset}>
                Try Again
              </Button>
              <Button variant="contained" onClick={this.handleReload}>
                Reload Page
              </Button>
            </Box>
          </Paper>
        </Box>
      )
    }

    return this.props.children
  }
}
