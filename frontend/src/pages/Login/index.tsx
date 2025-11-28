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
  Stack,
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
            <LinkWrappper to="/reset-password">
              Forgot your password?
            </LinkWrappper>
            <Stack
              flexDirection="row"
              gap={2}
              mt={3}
              alignItems="center"
              justifyContent="flex-end"
            ></Stack>

            <ButtonLogin data-testid="button-submit" type="submit">
              Sign In
            </ButtonLogin>

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

const LinkWrappper = styled(Link)({
  fontSize: 14,
  marginLeft: 6,
  color: "#1892d1",
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
