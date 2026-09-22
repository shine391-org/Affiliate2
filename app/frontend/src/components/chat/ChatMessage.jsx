import ReactMarkdown from "react-markdown"
import { Box, Typography, Avatar } from "@mui/material"
import SmartToyIcon from "@mui/icons-material/AutoAwesome"
import PersonIcon from "@mui/icons-material/PersonOutline"

const markdownComponents = {
  p: ({ children }) => (
    <Typography
      variant="body1"
      sx={{
        color: "text.primary",
        fontSize: "0.925rem",
        mb: 1,
        lineHeight: 1.7,
        "&:last-child": { mb: 0 },
      }}
    >
      {children}
    </Typography>
  ),
  strong: ({ children }) => (
    <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>{children}</Box>
  ),
  em: ({ children }) => (
    <Box component="span" sx={{ fontStyle: "italic" }}>{children}</Box>
  ),
  a: ({ href, children }) => (
    <Box
      component="a"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      sx={{ color: "primary.main", textDecoration: "none", fontWeight: 500, "&:hover": { textDecoration: "underline" } }}
    >
      {children}
    </Box>
  ),
  ul: ({ children }) => (
    <Box component="ul" sx={{ pl: 2.5, my: 0.75, "& li": { fontSize: "0.925rem", mb: 0.25, lineHeight: 1.7 } }}>
      {children}
    </Box>
  ),
  ol: ({ children }) => (
    <Box component="ol" sx={{ pl: 2.5, my: 0.75, "& li": { fontSize: "0.925rem", mb: 0.25, lineHeight: 1.7 } }}>
      {children}
    </Box>
  ),
  code: ({ children }) => (
    <Box
      component="code"
      sx={{
        bgcolor: "action.hover",
        px: 0.75,
        py: 0.25,
        borderRadius: 1,
        fontSize: "0.85rem",
        fontFamily: "'SF Mono', 'Fira Code', monospace",
        color: "primary.main",
      }}
    >
      {children}
    </Box>
  ),
  blockquote: ({ children }) => (
    <Box sx={{ borderLeft: 3, borderColor: "primary.main", pl: 2, my: 1, color: "text.secondary", opacity: 0.85 }}>
      {children}
    </Box>
  ),
  hr: () => <Box sx={{ borderTop: 1, borderColor: "divider", my: 1.5 }} />,
}

export default function ChatMessage({ role, content }) {
  const isUser = role === "user"
  const isSystem = role === "system"

  return (
    <Box
      sx={{
        display: "flex",
        gap: 1.5,
        py: 1,
        px: 1,
        maxWidth: 900,
        mx: "auto",
        width: "100%",
        justifyContent: isUser ? "flex-end" : "flex-start",
        animation: "messageIn 0.25s ease-out",
        "@keyframes messageIn": {
          from: { opacity: 0, transform: "translateY(6px)" },
          to: { opacity: 1, transform: "translateY(0)" },
        },
      }}
    >
      {/* Avatar — left side for assistant/system */}
      {!isUser && (
        <Avatar
          sx={{
            width: 30,
            height: 30,
            mt: 0.5,
            bgcolor: isSystem ? "action.hover" : "primary.main",
            color: isSystem ? "text.secondary" : "#fff",
            flexShrink: 0,
            boxShadow: isSystem ? "none" : (theme) => theme.customShadows?.sm,
          }}
        >
          <SmartToyIcon sx={{ fontSize: 16 }} />
        </Avatar>
      )}

      {/* Message bubble */}
      <Box
        sx={{
          flex: isUser ? "none" : 1,
          maxWidth: isUser ? "85%" : "100%",
          minWidth: 0,
        }}
      >
        <Typography
          variant="caption"
          sx={{
            fontWeight: 600,
            color: "text.secondary",
            fontSize: "0.7rem",
            mb: 0.5,
            display: "block",
            textAlign: isUser ? "right" : "left",
            letterSpacing: "0.02em",
            textTransform: "uppercase",
          }}
        >
          {isUser ? "You" : isSystem ? "System" : "ViralMint"}
        </Typography>

        <Box
          sx={{
            bgcolor: isUser
              ? "primary.main"
              : isSystem
                ? (theme) => theme.palette.mode === "dark" ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.03)"
                : (theme) => theme.palette.mode === "dark" ? "rgba(255,255,255,0.04)" : "background.paper",
            borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
            px: 2,
            py: 1.5,
            boxShadow: isUser ? (theme) => theme.customShadows?.md : (theme) => theme.customShadows?.sm,
            border: isUser ? "none" : 1,
            borderColor: "divider",
          }}
        >
          {isUser ? (
            <Typography
              variant="body1"
              sx={{
                color: "#fff",
                fontSize: "0.925rem",
                lineHeight: 1.7,
                whiteSpace: "pre-wrap",
              }}
            >
              {content}
            </Typography>
          ) : (
            <Box>
              <ReactMarkdown components={markdownComponents}>
                {content || ""}
              </ReactMarkdown>
            </Box>
          )}
        </Box>
      </Box>

      {/* Avatar — right side for user */}
      {isUser && (
        <Avatar
          sx={{
            width: 30,
            height: 30,
            mt: 0.5,
            bgcolor: "action.hover",
            color: "text.secondary",
            flexShrink: 0,
          }}
        >
          <PersonIcon sx={{ fontSize: 16 }} />
        </Avatar>
      )}
    </Box>
  )
}
