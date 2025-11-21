import { ChangeEvent, FormEvent, useState } from "react"
import { useDispatch } from "react-redux"
import { Link, useNavigate } from "react-router-dom"

import { AxiosError } from "axios"

import LockOutlinedIcon from "@mui/icons-material/LockOutlined"
import MailOutlineIcon from "@mui/icons-material/MailOutline"
import {
  Alert,
  Box,
  CircularProgress,
  Snackbar,
  styled,
  Typography,
} from "@mui/material"

interface ErrorResponse {
  detail?: string
}

import Loading from "components/common/Loading"
import PublicHeader from "components/PublicLayout/PublicHeader"
import { resendVerificationEmail } from "store/slice/Registration/RegistrationActions"
import { getMe, login } from "store/slice/User/UserActions"
import { AppDispatch } from "store/store"

const Login = () => {
  const navigate = useNavigate()
  const dispatch: AppDispatch = useDispatch()
  const [needsVerification, setNeedsVerification] = useState(false)
  const [resendingEmail, setResendingEmail] = useState(false)
  const [showResendSnackbar, setShowResendSnackbar] = useState(false)

  const [loading, setLoading] = useState(false)
  const [errors, setErrors] = useState<{ [key: string]: string }>({
    email: "",
    password: "",
  })
  const [values, setValues] = useState<{ email: string; password: string }>({
    email: "",
    password: "",
  })

  // Handle resend verification email
  const handleResendEmail = async () => {
    if (!values.email) {
      return
    }

    setResendingEmail(true)

    try {
      const resultAction = await dispatch(resendVerificationEmail(values.email))

      if (resendVerificationEmail.fulfilled.match(resultAction)) {
        setShowResendSnackbar(true)
      }
    } catch (error) {
      console.error("Failed to resend verification email:", error)
    } finally {
      setResendingEmail(false)
    }
  }

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const errorCheck = validateSubmit()
    if (errorCheck) return

    setLoading(true)
    setNeedsVerification(false)

    dispatch(login(values))
      .unwrap()
      .then(async (_) => {
        await dispatch(getMe())
        navigate("/console")
      })
      .catch((e: AxiosError) => {
        const status = e.response?.status
        const errorDetail = (e.response?.data as ErrorResponse)?.detail

        // Check for email verification error first
        if (status === 403 && errorDetail?.includes("not verified")) {
          setNeedsVerification(true)
          setErrors({
            email:
              errorDetail ||
              "Email address not verified. Please click the verification link sent to your email.",
            password: "",
          })
        } else if (status && status >= 400 && status < 500) {
          // Handle other 4xx errors (but exclude 403 verification errors)
          setErrors({ email: "Email or password is wrong.", password: "" })
        } else {
          // Handle unexpected errors
          setErrors({
            email: "An unexpected error occurred in authentication.",
            password: "",
          })
        }
      })
      .finally(() => {
        setLoading(false)
      })
  }

  const validateSubmit = () => {
    const errors = { email: "", password: "" }
    if (!values.email) {
      errors.email = "This field is required"
    }
    if (!values.password) {
      errors.password = "This field is required"
    }
    setErrors(errors)
    return errors.password || errors.email
  }

  const onChangeValue = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    setValues({ ...values, [name]: value })
    setErrors({ ...errors, [name]: !value ? "This field is required" : "" })
    // Clear verification flag when user changes input
    if (needsVerification) {
      setNeedsVerification(false)
    }
  }

  return (
    <>
      <PublicHeader />
      <LoginWrapper>
        <LoginContent>
          <CardHeader>
            <LogoWrapper>
              <Logo src="/static/optinist_logo.png" alt="OptiNiSt Logo" />
            </LogoWrapper>
            <Title data-testid="title">Login to OptiNiSt</Title>
            <Subtitle>Enter your credentials to access your account</Subtitle>
          </CardHeader>

          <FormSignUp autoComplete="off" onSubmit={onSubmit}>
            <Box sx={{ mb: 3 }}>
              <LabelField>Email</LabelField>
              <InputWrapper>
                <IconWrapper>
                  <MailOutlineIcon sx={{ fontSize: 18, color: "#9ca3af" }} />
                </IconWrapper>
                <Input
                  data-testid="email"
                  autoComplete="off"
                  error={!!errors.email}
                  name="email"
                  onChange={onChangeValue}
                  value={values.email}
                  placeholder="name@example.com"
                />
              </InputWrapper>
              {errors.email && (
                <TextError data-testid="error-email">{errors.email}</TextError>
              )}
            </Box>

            <Box sx={{ mb: 3 }}>
              <LabelField>Password</LabelField>
              <InputWrapper>
                <IconWrapper>
                  <LockOutlinedIcon sx={{ fontSize: 18, color: "#9ca3af" }} />
                </IconWrapper>
                <Input
                  data-testid="password"
                  autoComplete="off"
                  error={!!errors.password}
                  onChange={onChangeValue}
                  name="password"
                  type="password"
                  value={values.password}
                  placeholder="••••••••"
                />
              </InputWrapper>
              {errors.password && (
                <TextError data-testid="error-password">
                  {errors.password}
                </TextError>
              )}
            </Box>

            {/* Verification Alert */}
            {needsVerification && (
              <Alert
                severity="warning"
                sx={{ mb: 2, fontSize: 12 }}
                action={
                  <ResendButton
                    onClick={handleResendEmail}
                    disabled={resendingEmail}
                  >
                    {resendingEmail ? (
                      <>
                        <CircularProgress size={12} sx={{ mr: 0.5 }} />
                        Sending...
                      </>
                    ) : (
                      "Resend Email"
                    )}
                  </ResendButton>
                }
              >
                Please verify your email address to continue.
              </Alert>
            )}

            <ButtonLogin data-testid="button-submit" type="submit">
              Sign In
            </ButtonLogin>

            {/* Social login - Not implemented yet */}
            {/* <SeparatorWrapper>
            <Separator />
            <SeparatorText>Or continue with</SeparatorText>
            <Separator />
          </SeparatorWrapper>

          <SocialButtonsWrapper>
            <SocialButton type="button">
              <GoogleIcon />
              Google
            </SocialButton>
            <SocialButton type="button">
              <GitHubIcon />
              GitHub
            </SocialButton>
          </SocialButtonsWrapper> */}

            <SignUpWrapper>
              Don&apos;t have an account?{" "}
              <SignUpLink to="/register">Sign up</SignUpLink>
            </SignUpWrapper>
          </FormSignUp>
        </LoginContent>
        <Loading loading={loading} />

        {/* Resend success snackbar */}
        <Snackbar
          open={showResendSnackbar}
          autoHideDuration={6000}
          onClose={() => setShowResendSnackbar(false)}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <Alert
            onClose={() => setShowResendSnackbar(false)}
            severity="success"
            sx={{ width: "100%" }}
          >
            Verification email resent successfully
          </Alert>
        </Snackbar>
      </LoginWrapper>
    </>
  )
}

const LoginWrapper = styled(Box)({
  width: "100%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  paddingTop: 64,
})

const LoginContent = styled(Box)({
  width: "100%",
  maxWidth: 448,
  padding: "32px",
  backgroundColor: "#ffffff",
  border: "1px solid #e5e7eb",
  borderRadius: 8,
  boxShadow:
    "0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)",
})

const CardHeader = styled(Box)({
  marginBottom: 32,
  textAlign: "center",
})

const LogoWrapper = styled(Box)({
  display: "flex",
  justifyContent: "center",
  marginBottom: 24,
})

const Logo = styled("img")({
  height: 60,
  width: "auto",
})

const Title = styled(Typography)({
  fontSize: 24,
  fontWeight: 600,
  color: "#000000",
  marginBottom: 8,
})

const Subtitle = styled(Typography)({
  fontSize: 14,
  color: "#6b7280",
})

const FormSignUp = styled("form")({})

const LabelField = styled("label")({
  display: "block",
  fontSize: 14,
  fontWeight: 500,
  color: "#374151",
  marginBottom: 8,
})

const InputWrapper = styled(Box)({
  position: "relative",
  display: "flex",
  alignItems: "center",
})

const IconWrapper = styled(Box)({
  position: "absolute",
  left: 12,
  display: "flex",
  alignItems: "center",
  pointerEvents: "none",
})

const Input = styled("input", {
  shouldForwardProp: (props) => props !== "error",
})<{ error: boolean }>(({ error }) => {
  return {
    width: "100%",
    height: 40,
    borderRadius: 6,
    border: "1px solid",
    borderColor: error ? "#ef4444" : "#d1d5db",
    paddingLeft: 40,
    paddingRight: 12,
    backgroundColor: "#ffffff",
    color: "#000000",
    fontSize: 14,
    transition: "all 0.2s",
    outline: "none",
    boxSizing: "border-box",
    "::placeholder": {
      color: "#9ca3af",
    },
    ":focus": {
      borderColor: error ? "#ef4444" : "#000000",
      boxShadow: error
        ? "0 0 0 3px rgba(239, 68, 68, 0.1)"
        : "0 0 0 3px rgba(0, 0, 0, 0.1)",
    },
  }
})

const ButtonLogin = styled("button")({
  width: "100%",
  height: 40,
  backgroundColor: "#000000",
  color: "#ffffff",
  borderRadius: 6,
  border: "none",
  outline: "none",
  fontSize: 14,
  fontWeight: 500,
  cursor: "pointer",
  transition: "background-color 0.2s",
  ":hover": {
    backgroundColor: "#1f2937",
  },
  marginBottom: 16,
})

// Social login components - Commented out (not implemented yet)
// const SeparatorWrapper = styled(Box)({
//   display: "flex",
//   alignItems: "center",
//   marginTop: 24,
//   marginBottom: 16,
// })

// const Separator = styled(Box)({
//   flex: 1,
//   height: 1,
//   backgroundColor: "#e5e7eb",
// })

// const SeparatorText = styled("span")({
//   padding: "0 8px",
//   fontSize: 14,
//   color: "#6b7280",
//   backgroundColor: "#ffffff",
// })

// const SocialButtonsWrapper = styled(Box)({
//   display: "grid",
//   gridTemplateColumns: "1fr 1fr",
//   gap: 12,
//   marginBottom: 24,
// })

// const SocialButton = styled("button")({
//   display: "flex",
//   alignItems: "center",
//   justifyContent: "center",
//   height: 40,
//   backgroundColor: "#ffffff",
//   border: "1px solid #d1d5db",
//   borderRadius: 6,
//   color: "#374151",
//   fontSize: 14,
//   fontWeight: 500,
//   cursor: "pointer",
//   transition: "all 0.2s",
//   ":hover": {
//     backgroundColor: "#f9fafb",
//     color: "#000000",
//   },
// })

// const GoogleIcon = () => (
//   <svg style={{ width: 20, height: 20, marginRight: 8 }} viewBox="0 0 24 24">
//     <path
//       fill="#4285F4"
//       d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
//     />
//     <path
//       fill="#34A853"
//       d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
//     />
//     <path
//       fill="#FBBC05"
//       d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
//     />
//     <path
//       fill="#EA4335"
//       d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
//     />
//   </svg>
// )

// const GitHubIcon = () => (
//   <svg
//     style={{ width: 20, height: 20, marginRight: 8 }}
//     fill="currentColor"
//     viewBox="0 0 24 24"
//   >
//     <path d="M12 2C6.477 2 2 6.477 2 12c0 4.42 2.865 8.17 6.839 9.49.5.092.682-.217.682-.482 0-.237-.008-.866-.013-1.7-2.782.603-3.369-1.34-3.369-1.34-.454-1.156-1.11-1.463-1.11-1.463-.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.578 9.578 0 0112 6.836c.85.004 1.705.114 2.504.336 1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.203 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.842-2.339 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.336-.012 2.415-.012 2.743 0 .267.18.578.688.48C19.138 20.167 22 16.418 22 12c0-5.523-4.477-10-10-10z" />
//   </svg>
// )

const SignUpWrapper = styled(Typography)({
  textAlign: "center",
  fontSize: 14,
  color: "#6b7280",
})

const SignUpLink = styled(Link)({
  color: "#000000",
  textDecoration: "none",
  fontWeight: 500,
  ":hover": {
    textDecoration: "underline",
  },
})

const ResendButton = styled("button")({
  backgroundColor: "transparent",
  color: "#ed6c02",
  border: "1px solid #ed6c02",
  borderRadius: 4,
  padding: "4px 12px",
  fontSize: 12,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  fontWeight: 500,
  transition: "all 0.2s",
  "&:hover": {
    backgroundColor: "rgba(237, 108, 2, 0.08)",
  },
  "&:disabled": {
    opacity: 0.6,
    cursor: "not-allowed",
  },
})

const TextError = styled(Typography)({
  fontSize: 12,
  color: "#ef4444",
  marginTop: 4,
  wordWrap: "break-word",
  wordBreak: "break-word",
  whiteSpace: "normal",
  width: "100%",
})

export default Login
