#!/usr/bin/env python3

"""
Comprehensive Priority Queue Test

This script provides end-to-end testing of priority queue functionality including:
- Environment setup and JWT token generation
- Workspace creation for premium and free users
- Sample data provisioning
- Workflow submission with priority queue testing
- Detailed execution monitoring and reporting

Consolidates functionality from:
- run_priority_queue_test.sh (environment setup)
- test-workflow-tutorial1-post.sh (workflow submission)
- priority_queue_simple_test.py (monitoring and reporting)

Usage:
  # Local testing with default settings
  python priority_queue_simple_test.py

  # Cloud testing
  python priority_queue_simple_test.py --environment cloud --api-url <your-api-url>

  # Quick submission-only test
  python priority_queue_simple_test.py --submission-only

  # Custom workflow counts
  python priority_queue_simple_test.py --premium-count 5 --free-count 5
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
import yaml

try:
    import psutil
except ImportError:
    psutil = None
    print("psutil not available - process checking will be limited")


class SimplePriorityQueueTester:
    """Comprehensive priority queue tester with environment setup and API testing"""

    def __init__(
        self,
        base_url: str = "http://localhost:8002",
        setup_sample_data: bool = True,
        ensure_env: bool = True,
        environment: str = "local",
        create_workspaces: bool = True,
        skip_token_generation: bool = False,
        debug_tokens: bool = False,
    ):
        # Set environment variable to skip storage checks during testing
        import os

        os.environ["SKIP_STORAGE_CHECKS"] = "true"

        self.base_url = base_url
        self.environment = environment
        self.skip_token_generation = skip_token_generation
        self.debug_tokens = debug_tokens
        self.default_timeout = (
            1800  # Default timeout for workflow monitoring (30 minutes)
        )
        self.premium_workspace_id = None
        self.free_workspace_id = None
        self.report_filename = None  # Will be set when first report is generated

        if ensure_env:
            self.ensure_environment_setup()

        self.tokens = self.load_tokens()

        if create_workspaces:
            self.create_test_workspaces()

        if setup_sample_data:
            self.setup_sample_data_for_workspaces()

    def ensure_environment_setup(self):
        """Ensure conda environment and poetry dependencies are properly set up"""
        print("Ensuring environment setup...")

        # Check if we're in the right directory
        project_root = Path(__file__).parent.parent.parent
        os.chdir(project_root)

        # Ensure poetry dependencies are installed
        try:
            result = subprocess.run(
                ["poetry", "install"], capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                print(f"Poetry install warning: {result.stderr}")
            else:
                print("Poetry dependencies verified")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"Could not run poetry install: {e}")

        # Check conda environment (optional)
        try:
            result = subprocess.run(
                ["conda", "env", "list"], capture_output=True, text=True, timeout=30
            )
            if "snakemake_up" in result.stdout:
                print("Found snakemake_up conda environment")

                # Check and install CBC solver if missing
                # self.ensure_cbc_solver()
            else:
                print("snakemake_up conda environment not found - workflows may fail")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("Conda not available - using system environment")

    def load_tokens(self) -> Dict:
        """Load JWT tokens from tokens.json, generating fresh tokens if needed"""
        tokens_file = Path(__file__).parent / "tokens.json"

        # Generate fresh tokens unless skipped
        if not self.skip_token_generation:
            print("🔑 Generating fresh JWT tokens...")
            try:
                cmd = [
                    "python",
                    str(Path(__file__).parent / "get_jwt_tokens.py"),
                    "--environment",
                    self.environment,
                    "--output-file",
                    str(tokens_file),
                ]

                # Add API URL for cloud environment
                if (
                    self.environment == "cloud"
                    and self.base_url != "http://localhost:8002"
                ):
                    cmd.extend(["--api-url", self.base_url])

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if result.returncode == 0:
                    print("Fresh tokens generated successfully")
                else:
                    print(f"Token generation warning: {result.stderr}")
                    print(f"Command output: {result.stdout}")
            except subprocess.TimeoutExpired:
                print("Token generation timed out")
            except Exception as e:
                print(f"Token generation failed: {e}")
        else:
            print("Skipping JWT token generation, using existing tokens...")

        if not tokens_file.exists():
            raise FileNotFoundError(
                "tokens.json not found. "
                "Either generate tokens first or run without --skip-token-gen"
            )

        try:
            with open(tokens_file, "r") as f:
                tokens = json.load(f)

            # Check if tokens is None or not a dictionary
            if tokens is None:
                raise ValueError("tokens.json contains null value")
            if not isinstance(tokens, dict):
                raise ValueError(
                    f"tokens.json must contain a JSON object, got {type(tokens)}"
                )

            # Add JWT debugging - analyze tokens for both user types
            if self.debug_tokens:
                print("\n JWT TOKEN ANALYSIS:")
                print("=" * 50)

                for user_type in ["premium", "free"]:
                    if (
                        user_type in tokens
                        and tokens[user_type] is not None
                        and "access_token" in tokens[user_type]
                    ):
                        token = tokens[user_type]["access_token"]
                        self.decode_jwt_token(token, user_type)
                        print()  # Empty line between user types
                    else:
                        print(f"No {user_type} access token found in tokens.json")

                # Compare tokens and highlight differences
                print("TOKEN COMPARISON:")
                print("-" * 50)

                premium_token = None
                free_token = None

                if (
                    "premium" in tokens
                    and tokens["premium"] is not None
                    and "access_token" in tokens["premium"]
                ):
                    premium_token = self.decode_jwt_token(
                        tokens["premium"]["access_token"], "premium"
                    )
                if (
                    "free" in tokens
                    and tokens["free"] is not None
                    and "access_token" in tokens["free"]
                ):
                    free_token = self.decode_jwt_token(
                        tokens["free"]["access_token"], "free"
                    )

                if premium_token and free_token:
                    print("Key differences between Premium and Free tokens:")

                    # Compare important fields
                    premium_payload = premium_token["payload"]
                    free_payload = free_token["payload"]

                    for field in ["email", "user_id", "iss", "aud"]:
                        if field in premium_payload and field in free_payload:
                            if premium_payload[field] != free_payload[field]:
                                print(f"{field}:")
                                prem_val = premium_payload[field]
                                free_val = free_payload[field]
                                print(f"Premium: {prem_val}")
                                print(f"Free:    {free_val}")

                    # Check for fields that exist in one but not the other
                    premium_only = set(premium_payload.keys()) - set(
                        free_payload.keys()
                    )
                    free_only = set(free_payload.keys()) - set(premium_payload.keys())

                    if premium_only:
                        print(f"Fields only in Premium token: {list(premium_only)}")
                    if free_only:
                        print(f"Fields only in Free token: {list(free_only)}")

                    if not premium_only and not free_only:
                        print("Both tokens have the same field structure")

                print("=" * 50)
            return tokens

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid tokens.json file: {e}")

    def load_workflow_data(self) -> Dict:
        """Load the test workflow data"""
        workflow_file = Path(__file__).parent / "test-workflow-tutorial1-postdata.json"
        if not workflow_file.exists():
            raise FileNotFoundError(f"Workflow data file not found: {workflow_file}")

        with open(workflow_file, "r") as f:
            return json.load(f)

    def check_server_health(self) -> bool:
        """Check if the server is running and responding"""
        try:
            print(f"Checking server connectivity at {self.base_url}...")

            # Try a simple GET request to a health endpoint or root
            import requests

            response = requests.get(f"{self.base_url}/", timeout=5)

            if response.status_code in [
                200,
                404,
            ]:  # 404 is fine, means server is responding
                print(f"Server is responding (HTTP {response.status_code})")
                return True
            else:
                print(f"Server returned HTTP {response.status_code}")
                return True  # Still responding, might work

        except requests.exceptions.ConnectionError:
            print(f"Cannot connect to server at {self.base_url}")
            print(" → Is main.py running?")
            print(" → Check if the server is started on the correct port")
            return False
        except requests.exceptions.Timeout:
            print(f"Server timeout at {self.base_url}")
            print(" → Server is not responding within 5 seconds")
            print(" → Check if the server is overloaded or stuck")
            return False
        except Exception as e:
            print(f"Unexpected error checking server: {e}")
            print(" → Check server configuration and network connectivity")
            return False

    def create_test_workspaces(self):
        """Create test workspaces for premium and free users"""
        print("Creating test workspaces...")

        # First, check if server is running
        if not self.check_server_health():
            print("Server health check failed - cannot proceed with workspace creation")
            self.premium_workspace_id = None
            self.free_workspace_id = None
            return

        try:
            # Create workspace for premium user
            print("Creating workspace for premium user...")
            premium_data = {"name": "Premium Priority Testing"}
            premium_headers = {
                "Authorization": f"Bearer {self.tokens['premium']['access_token']}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                f"{self.base_url}/workspace", json=premium_data, headers=premium_headers
            )

            if response.status_code == 200:
                response_data = response.json()
                self.premium_workspace_id = response_data["id"]
                print(f"Premium workspace created: ID {self.premium_workspace_id}")
            else:
                print(f"Failed to create premium workspace: {response.status_code}")
                if response.status_code == 401:
                    print(
                        "→ Authentication failed: Check if premium user token is valid"
                    )
                elif response.status_code == 403:
                    print(
                        "→Permission denied: Premium user lacks "
                        "workspace creation rights"
                    )
                elif response.status_code == 500:
                    print("→ Server error: Check main.py logs for details")
                    print(f" → Response: {response.text}")
                else:
                    print(f" → Response: {response.text}")
                self.premium_workspace_id = None

            # Create workspace for free user
            print("Creating workspace for free user...")
            free_data = {"name": "Free Priority Testing"}
            free_headers = {
                "Authorization": f"Bearer {self.tokens['free']['access_token']}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                f"{self.base_url}/workspace", json=free_data, headers=free_headers
            )

            if response.status_code == 200:
                response_data = response.json()
                self.free_workspace_id = response_data["id"]
                print(f"Free workspace created: ID {self.free_workspace_id}")
            else:
                print(f"Failed to create free workspace: {response.status_code}")
                if response.status_code == 401:
                    print(" → Authentication failed: Check if free user token is valid")
                elif response.status_code == 403:
                    print(
                        " → Permission denied: Free user lacks "
                        "workspace creation rights"
                    )
                elif response.status_code == 500:
                    print(" → Server error: Check main.py logs for details")
                    print(f" → Response: {response.text}")
                else:
                    print(f" → Response: {response.text}")
                self.free_workspace_id = None

            # Report results
            created_workspaces = []
            if self.premium_workspace_id:
                created_workspaces.append(f"Premium: {self.premium_workspace_id}")
            if self.free_workspace_id:
                created_workspaces.append(f"Free: {self.free_workspace_id}")

            if created_workspaces:
                print("Workspace creation completed!")
                print(f"Created: {', '.join(created_workspaces)}")
            else:
                print("No workspaces were created successfully - will use defaults")

        except requests.exceptions.ConnectionError as e:
            print(f"Connection error during workspace creation: {e}")
            print("→ Server appears to be down or unreachable")
            print("→ Check if main.py is running and accessible")
            self.premium_workspace_id = None
            self.free_workspace_id = None
        except requests.exceptions.Timeout as e:
            print(f"Timeout error during workspace creation: {e}")
            print("→ Server is not responding in time")
            print("→ Check server performance and load")
            self.premium_workspace_id = None
            self.free_workspace_id = None
        except Exception as e:
            print(f"Unexpected error during workspace creation: {e}")
            print("→ Check server logs for detailed error information")
            # Ensure workspace IDs are None on exception
            self.premium_workspace_id = None
            self.free_workspace_id = None

    def setup_sample_data_for_workspaces(self):
        """Copy sample data to workspace directories to ensure workflows can run"""
        print("Setting up sample data...")

        try:
            # Get project root and sample data directories
            project_root = Path(__file__).parent.parent.parent
            sample_data_dir = project_root / "sample_data" / "tutorial"

            # Use OPTINIST_DIR if set, otherwise default to /tmp/studio
            data_dir = Path(os.environ.get("OPTINIST_DIR", "/tmp/studio"))

            print(f"Project root: {project_root}")
            print(f"Sample data source: {sample_data_dir}")
            print(f"Data directory: {data_dir}")

            if not sample_data_dir.exists():
                print(f"Sample data directory not found: {sample_data_dir}")
                return False

            # Determine workspace IDs to setup - only use created workspaces
            workspace_ids = []
            if self.premium_workspace_id:
                workspace_ids.append(self.premium_workspace_id)
            if self.free_workspace_id:
                workspace_ids.append(self.free_workspace_id)

            # If no workspaces were created, cannot set up sample data
            if not workspace_ids:
                print("No workspaces were created - cannot set up sample data")
                print("Either workspace creation failed or was skipped")
                print(f"Premium workspace ID: {self.premium_workspace_id}")
                print(f"Free workspace ID: {self.free_workspace_id}")
                return False

            print(f"Setting up sample data for {len(workspace_ids)} workspaces...")

            for workspace_id in workspace_ids:
                print(f"Setting up workspace {workspace_id}...")

                # Create directories
                input_dir = data_dir / "input" / str(workspace_id)
                output_dir = data_dir / "output" / str(workspace_id)

                input_dir.mkdir(parents=True, exist_ok=True)
                output_dir.mkdir(parents=True, exist_ok=True)

                # Copy input data only
                sample_input_dir = sample_data_dir / "input"
                if sample_input_dir.exists():
                    for sample_file in sample_input_dir.iterdir():
                        if sample_file.is_file():
                            target_file = input_dir / sample_file.name
                            if not target_file.exists():
                                shutil.copy2(sample_file, target_file)
                    print(f"Copied input data to workspace {workspace_id}")
                else:
                    print(f"No input data found at {sample_input_dir}")

                print(f"Prepared workspace {workspace_id} (input data only)")

            print("Sample data setup completed!")
            return True

        except Exception as e:
            print(f"Failed to setup sample data: {e}")
            return False

    def check_workflow_status_by_files(
        self, workflow_id: str, workspace_id: int
    ) -> Dict:
        """Check workflow status by examining filesystem and process files"""
        try:
            data_dir = Path(os.environ.get("OPTINIST_DIR", "/tmp/studio"))
            workflow_dir = data_dir / "output" / str(workspace_id) / workflow_id

            if not workflow_dir.exists():
                return {
                    "status": "running",
                    "running": True,
                    "completed": False,
                    "reason": "Workflow directory not found - may not have started yet",
                }

            # PRIMARY CHECK: Look for experiment.yaml (definitive completion indicator)
            experiment_yaml = workflow_dir / "experiment.yaml"
            if experiment_yaml.exists():
                try:
                    with open(experiment_yaml, "r") as f:
                        content = f.read()

                    if "finished_at:" in content:
                        # Workflow has finished - check actual success status
                        if "success: success" in content:
                            return {
                                "status": "success",
                                "running": False,
                                "completed": True,
                                "reason": "Workflow completed successfully "
                                "(experiment.yaml)",
                            }
                        elif "success: error" in content:
                            return {
                                "status": "failed",
                                "running": False,
                                "completed": True,
                                "reason": "Workflow completed with errors "
                                "(experiment.yaml)",
                            }
                        elif "success: running" in content:
                            # Has finished_at but is still "running"- in progress
                            return {
                                "status": "running",
                                "running": True,
                                "completed": False,
                                "reason": "Workflow still running "
                                "(experiment.yaml shows success: running)",
                            }
                        else:
                            # Has finished_at but unknown success status
                            return {
                                "status": "running",
                                "running": True,
                                "completed": False,
                                "reason": "Workflow status unknown "
                                "(experiment.yaml has finished_at but unclear success)",
                            }
                    else:
                        # Has experiment.yaml but no finished_at = still running
                        # Check if success status indicates running
                        if "success: running" in content:
                            return {
                                "status": "running",
                                "running": True,
                                "completed": False,
                                "reason": "Workflow running "
                                "(experiment.yaml shows success: "
                                "running, no finished_at)",
                            }
                        else:
                            return {
                                "status": "running",
                                "running": True,
                                "completed": False,
                                "reason": "Workflow running "
                                "(experiment.yaml exists but no finished_at)",
                            }
                except Exception as e:
                    print(f"Could not read experiment.yaml: {e}")
                    pass

            pid_json_file = workflow_dir / "pid.json"
            if pid_json_file.exists():
                try:
                    with open(pid_json_file, "r") as f:
                        pid_data = json.load(f)

                    if "last_pid" in pid_data and psutil:
                        pid = pid_data["last_pid"]
                        if psutil.pid_exists(pid):
                            return {
                                "status": "running",
                                "running": True,
                                "completed": False,
                                "reason": f"Process {pid} is running (pid.json)",
                            }
                except Exception:
                    pass

            # Check for output files as completion indicator
            pkl_files = list(workflow_dir.rglob("*.pkl"))
            if pkl_files:
                # Filter out log-related pickle files
                output_pkl_files = [
                    f
                    for f in pkl_files
                    if not any(x in str(f) for x in [".snakemake", "log"])
                ]
                if output_pkl_files:
                    return {
                        "status": "success",
                        "running": False,
                        "completed": True,
                        "reason": f"Workflow completed successfully - "
                        f"found {len(output_pkl_files)} output files",
                    }

            # Check snakemake logs for status
            snakemake_log_files = list(
                workflow_dir.glob(".snakemake/log/*.snakemake.log")
            )
            if snakemake_log_files:
                # Use the most recent log file
                latest_log = max(snakemake_log_files, key=lambda f: f.stat().st_mtime)
                try:
                    with open(latest_log, "r") as f:
                        log_content = f.read()

                    # Look for explicit failure indicators first
                    if any(
                        phrase in log_content
                        for phrase in [
                            "MissingOutputException",
                            "WorkflowError",
                            "CalledProcessError",
                            "Exiting because a job execution failed",
                            "At least one job did not complete successfully",
                        ]
                    ):
                        return {
                            "status": "failed",
                            "running": False,
                            "completed": True,
                            "reason": "Workflow failed - found error "
                            "patterns in snakemake.log",
                        }

                    # Look for successful completion indicators
                    elif any(
                        phrase in log_content
                        for phrase in [
                            "Finished job 0.",
                            "jobs) finished",
                            "Terminating processes",
                        ]
                    ):
                        return {
                            "status": "success",
                            "running": False,
                            "completed": True,
                            "reason": "Workflow completed successfully",
                        }

                    # Look for running indicators
                    elif any(
                        phrase in log_content
                        for phrase in [
                            "Building DAG",
                            "Select jobs to execute",
                            "Execute.*jobs",
                        ]
                    ) and not any(
                        phrase in log_content
                        for phrase in ["ERROR", "Exception", "Failed"]
                    ):
                        return {
                            "status": "running",
                            "running": True,
                            "completed": False,
                            "reason": "Workflow is actively running",
                        }
                except Exception:
                    pass

            # Final fallback: check directory age and assume completion if old enough
            dir_age = time.time() - workflow_dir.stat().st_mtime
            if dir_age < 300:  # Less than 5 minutes old - might still be running
                return {
                    "status": "running",
                    "running": True,
                    "completed": False,
                    "reason": "Workflow directory is recent, assuming still running",
                }
            else:
                # Directory is old - likely finished, assume success if no errors found
                return {
                    "status": "success",
                    "running": False,
                    "completed": True,
                    "reason": f"Workflow directory is {int(dir_age/60)} "
                    f"minutes old, assuming finished successfully",
                }

        except Exception as e:
            return {
                "status": "error",
                "running": None,
                "completed": None,
                "error": str(e),
            }

    def read_workflow_error_log(
        self, workspace_id: int, workflow_id: str
    ) -> Optional[str]:
        """Read error log for a failed workflow"""
        try:
            data_dir = Path(os.environ.get("OPTINIST_DIR", "/tmp/studio"))
            workflow_dir = data_dir / "output" / str(workspace_id) / workflow_id
            error_content = []

            # Try to read the dedicated error log first
            error_log_path = workflow_dir / "error.log"
            if error_log_path.exists():
                with open(error_log_path, "r") as f:
                    content = f.read().strip()
                    if content:
                        error_content.append("=== ERROR.LOG ===")
                        error_content.append(content)

            # Look for errors in snakemake.log
            snakemake_log_path = workflow_dir / "snakemake.log"
            if snakemake_log_path.exists():
                with open(snakemake_log_path, "r") as f:
                    log_content = f.read()

                    # Extract relevant error information
                    error_patterns = [
                        "ERROR",
                        "Error",
                        "Missing input files",
                        "WorkflowError",
                        "Exception",
                        "Failed",
                        "CalledProcessError",
                        "Traceback",
                    ]

                    error_lines = []
                    lines = log_content.split("\n")

                    for i, line in enumerate(lines):
                        if any(pattern in line for pattern in error_patterns):
                            # Include some context around the error
                            start = max(0, i - 2)
                            end = min(len(lines), i + 3)
                            error_section = lines[start:end]
                            error_lines.extend(error_section)

                    if error_lines:
                        # Remove duplicates while preserving order
                        seen = set()
                        unique_lines = []
                        for line in error_lines:
                            if line not in seen:
                                unique_lines.append(line)
                                seen.add(line)

                        if error_content:
                            error_content.append("=== SNAKEMAKE.LOG ERRORS ===")
                        error_content.extend(
                            unique_lines[-10:]
                        )  # Last 10 unique error-related lines

            return "\n".join(error_content) if error_content else None
        except Exception as e:
            return f"Could not read error log: {e}"

    def get_workflow_output_files(
        self, workspace_id: int, workflow_id: str
    ) -> List[str]:
        """Get list of output files produced by the workflow"""
        try:
            data_dir = Path(os.environ.get("OPTINIST_DIR", "/tmp/studio"))
            workflow_dir = data_dir / "output" / str(workspace_id) / workflow_id
            if not workflow_dir.exists():
                return []

            output_files = []

            # Find all .pkl files (main outputs)
            pkl_files = list(workflow_dir.rglob("*.pkl"))
            for pkl_file in pkl_files:
                # Skip log-related pickle files
                if not any(x in str(pkl_file) for x in [".snakemake", "log"]):
                    output_files.append(str(pkl_file.relative_to(workflow_dir)))

            # Add log files for reference
            log_files = list(workflow_dir.rglob("*.log"))
            for log_file in log_files:
                output_files.append(str(log_file.relative_to(workflow_dir)))

            return sorted(output_files)
        except Exception as e:
            return [f"Error reading outputs: {e}"]

    def wait_for_workflow_completion(
        self, workflow_id: str, user_type: str, workspace_id: int, timeout: int = 1800
    ) -> Dict:
        """Wait for workflow to complete and return completion status"""
        print(
            f"Waiting for {user_type} workflow {workflow_id} "
            f"to complete (timeout: {timeout}s)..."
        )

        start_time = time.time()
        last_status = None
        last_progress_time = start_time

        while (time.time() - start_time) < timeout:
            status_result = self.check_workflow_status_by_files(
                workflow_id, workspace_id
            )

            if status_result["status"] == "error":
                return {
                    "completed": True,
                    "success": False,
                    "error": f"Status check failed: {status_result['error']}",
                    "execution_time": time.time() - start_time,
                }

            if status_result["status"] == "failed":
                return {
                    "completed": True,
                    "success": False,
                    "error": f"Workflow failed: {status_result['reason']}",
                    "execution_time": time.time() - start_time,
                }

            is_running = status_result["running"]
            is_completed = status_result.get("completed", False)

            # Show progress every 30 seconds
            current_time = time.time()
            if current_time - last_progress_time >= 30:
                elapsed = int(current_time - start_time)
                remaining = int(timeout - elapsed)
                print(
                    f"{user_type} workflow {workflow_id}: {elapsed}s "
                    f"elapsed, {remaining}s remaining..."
                )
                last_progress_time = current_time

            if last_status != is_running:
                if is_running:
                    elapsed = int(current_time - start_time)
                    print(
                        f"{user_type} workflow {workflow_id} is "
                        f"running ({elapsed}s elapsed)..."
                    )
                elif is_completed:
                    elapsed = int(current_time - start_time)
                    print(
                        f"{user_type} workflow {workflow_id} "
                        f"finished ({elapsed}s elapsed)"
                    )
                last_status = is_running

            if is_completed:
                # Workflow finished, check if it was successful
                execution_time = time.time() - start_time

                # Look for error logs to determine success/failure
                error_log = self.read_workflow_error_log(workspace_id, workflow_id)
                output_files = self.get_workflow_output_files(workspace_id, workflow_id)

                if error_log and (
                    "ERROR" in error_log or "Missing input files" in error_log
                ):
                    return {
                        "completed": True,
                        "success": False,
                        "error": error_log,
                        "execution_time": execution_time,
                        "output_files": output_files,
                    }
                else:
                    return {
                        "completed": True,
                        "success": True,
                        "error": None,
                        "execution_time": execution_time,
                        "output_files": output_files,
                    }

            time.sleep(2)  # Check every 2 seconds

        return {
            "completed": False,
            "success": False,
            "error": f"Workflow timed out after {timeout} seconds",
            "execution_time": timeout,
            "output_files": self.get_workflow_output_files(workspace_id, workflow_id),
        }

    def submit_and_monitor_workflow(
        self,
        user_type: str,
        workspace_id: int,
        workflow_num: int,
        monitor_execution: bool = True,
    ) -> Dict:
        """Submit a workflow and optionally monitor its execution to completion"""

        print(
            f"Submitting {user_type} workflow #{workflow_num} "
            f"to workspace {workspace_id}"
        )

        # Pre-submission validation
        try:
            data_dir = Path(os.environ.get("OPTINIST_DIR", "/tmp/studio"))
            input_dir = data_dir / "input" / str(workspace_id)
            if not input_dir.exists():
                print(f"Warning: Input directory does not exist: {input_dir}")
            else:
                input_files = list(input_dir.glob("*"))
                print(f"Workspace {workspace_id} has {len(input_files)} input files")
        except Exception as e:
            print(f"Could not validate workspace {workspace_id}: {e}")

        # Get the appropriate token
        token = self.tokens[user_type]["access_token"]

        # Load and modify the workflow data
        workflow_data = self.load_workflow_data()
        workflow_data[
            "name"
        ] = f"{user_type}_priority_test_{workflow_num}_{int(time.time())}"

        # Set up headers and URL
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        url = f"{self.base_url}/run/{workspace_id}"

        # Enhanced debugging (only if debug flag is set)
        if self.debug_tokens:
            print(f"Enhanced debugging for {user_type} user workflow submission:")
            print(f"Token length: {len(token)} characters")
            print(f"Token starts: {token[:50]}...")
            print(f"Token ends: ...{token[-20:]}")
            print(f"Workspace ID: {workspace_id}")
            print(f"Workflow name: {workflow_data['name']}")
            print(f"Request URL: {url}")
            print(f"Headers: {dict(headers)}")
            print(f"Payload keys: {list(workflow_data.keys())}")
            print(f"Payload size: {len(json.dumps(workflow_data))} bytes")

        # Record timing
        submission_start = time.time()

        try:
            response = requests.post(url, json=workflow_data, headers=headers)
            submission_end = time.time()
            submission_duration = submission_end - submission_start

            if response.status_code == 200:
                workflow_id = response.text.strip('"')  # Remove quotes from response
                print(
                    f"{user_type} workflow #{workflow_num} submitted "
                    f"successfully: {workflow_id}"
                )

                result = {
                    "submission_success": True,
                    "workflow_id": workflow_id,
                    "user_type": user_type,
                    "workspace_id": workspace_id,
                    "workflow_num": workflow_num,
                    "submission_time": submission_start,
                    "submission_duration": submission_duration,
                    "status_code": response.status_code,
                    "response": workflow_id,
                    "submission_error": None,
                    "execution_success": None,
                    "execution_time": None,
                    "execution_error": None,
                    "overall_success": None,
                }

                # Monitor execution if requested
                if monitor_execution:
                    execution_result = self.wait_for_workflow_completion(
                        workflow_id,
                        user_type,
                        workspace_id,
                        timeout=self.default_timeout,
                    )

                    result.update(
                        {
                            "execution_success": execution_result["success"],
                            "execution_time": execution_result["execution_time"],
                            "execution_error": execution_result["error"],
                            "output_files": execution_result.get("output_files", []),
                            "overall_success": execution_result[
                                "success"
                            ],  # Overall success is execution success
                        }
                    )

                    if execution_result["success"]:
                        print(
                            f"{user_type} workflow #{workflow_num} "
                            f"completed successfully!"
                        )
                    else:
                        print(
                            f"{user_type} workflow #{workflow_num} failed: "
                            f"{execution_result['error']}"
                        )
                else:
                    result["overall_success"] = True  # Only submission was requested

                return result

            else:
                print(
                    f"{user_type} workflow #{workflow_num} submission failed: "
                    f"{response.status_code} - {response.text}"
                )

                # Add detailed debugging for HTTP 500 errors
                if response.status_code == 500:
                    print("Debug info for HTTP 500 error:")
                    print(f"- Workspace ID: {workspace_id}")
                    print(f"- User type: {user_type}")
                    print(f"- Request URL: {url}")
                    print(f"- Workflow name: {workflow_data.get('name', 'Unknown')}")

                    # Check if workspace has sample data
                    try:
                        data_dir = Path(os.environ.get("OPTINIST_DIR", "/tmp/studio"))
                        input_dir = data_dir / "input" / str(workspace_id)
                        if input_dir.exists():
                            input_files = list(input_dir.glob("*"))
                            print(
                                f" - Input files in workspace: {len(input_files)} files"
                            )
                            if input_files:
                                print(
                                    f" - Sample files: "
                                    f"{[f.name for f in input_files[:3]]}"
                                )
                        else:
                            print(f"-   Input directory not found: {input_dir}")
                    except Exception as e:
                        print(f"- Could not check workspace files: {e}")

                    # Try to parse error details from response
                    try:
                        error_details = json.loads(response.text)
                        if (
                            isinstance(error_details, dict)
                            and "detail" in error_details
                        ):
                            print(f"- Error detail: {error_details['detail']}")
                    except Exception:
                        print(f"- Raw response: {response.text}")

                    print(f"- Headers sent: {dict(headers)}")
                    print(f"- Payload size: {len(json.dumps(workflow_data))} bytes")
                return {
                    "submission_success": False,
                    "workflow_id": None,
                    "user_type": user_type,
                    "workspace_id": workspace_id,
                    "workflow_num": workflow_num,
                    "submission_time": submission_start,
                    "submission_duration": submission_duration,
                    "status_code": response.status_code,
                    "response": response.text,
                    "submission_error": f"HTTP {response.status_code}: {response.text}",
                    "execution_success": None,
                    "execution_time": None,
                    "execution_error": None,
                    "overall_success": False,
                }

        except Exception as e:
            submission_end = time.time()
            submission_duration = submission_end - submission_start
            print(f"{user_type} workflow #{workflow_num} submission exception: {e}")

            return {
                "submission_success": False,
                "workflow_id": None,
                "user_type": user_type,
                "workspace_id": workspace_id,
                "workflow_num": workflow_num,
                "submission_time": submission_start,
                "submission_duration": submission_duration,
                "status_code": None,
                "response": None,
                "submission_error": str(e),
                "execution_success": None,
                "execution_time": None,
                "execution_error": None,
                "overall_success": False,
            }

    def run_priority_test(
        self,
        premium_count: int = 3,
        free_count: int = 3,
        premium_workspace: Optional[int] = None,
        free_workspace: Optional[int] = None,
        parallel: bool = False,
        monitor_execution: bool = True,
    ) -> List[Dict]:
        """Run the priority queue test"""

        # Use created workspace IDs or provided values - no hardcoded fallbacks
        if premium_count > 0:  # Only validate premium workspace if we need it
            if premium_workspace is None:
                if self.premium_workspace_id:
                    premium_workspace = self.premium_workspace_id
                else:
                    raise ValueError(
                        "No premium workspace available. "
                        "Either workspace creation failed or --premium-workspace"
                        " must be specified"
                    )

        if free_workspace is None:
            if self.free_workspace_id:
                free_workspace = self.free_workspace_id
            else:
                raise ValueError(
                    "No free workspace available. "
                    "Either workspace creation failed or --free-workspace"
                    " must be specified"
                )

        if premium_count == 0:
            print("Starting Free Workspace Isolation Test")
            print("Testing ONLY free workspace workflows (no premium competition)")
        else:
            print("Starting Priority Queue API Test")

        print(f"Environment: {self.environment}")
        print(f"Base URL: {self.base_url}")

        if premium_count > 0:
            print(f"Premium workflows: {premium_count} (workspace {premium_workspace})")
            print(
                f"Using workspace IDs - Premium: {premium_workspace}, "
                f"Free: {free_workspace}"
            )
        else:
            print(f"Premium workflows: {premium_count} (SKIPPED for isolation test)")
            print(f"Using workspace ID - Free: {free_workspace}")

        print(f"Free workflows: {free_count} (workspace {free_workspace})")
        print(f"Parallel submission: {parallel}")
        print(f"Monitor execution: {monitor_execution}")
        if self.premium_workspace_id:
            print(f"(Created premium workspace: {self.premium_workspace_id})")
        if self.free_workspace_id:
            print(f"(Created free workspace: {self.free_workspace_id})")
        print()

        # Validate workspace setups before starting tests
        print("Validating workspace configurations...")

        if premium_count > 0:
            premium_valid = self.validate_workspace_setup(premium_workspace, "premium")
        else:
            premium_valid = True  # Skip premium validation in free-only mode
            print("Skipping premium workspace validation (free-only test mode)")

        free_valid = self.validate_workspace_setup(free_workspace, "free")

        if premium_count > 0:
            if not premium_valid or not free_valid:
                print("Some workspaces failed validation. Submissions may fail.")
                print(
                    "This may explain HTTP 500 errors if workspaces lack required data."
                )
            else:
                print("Both workspaces validated successfully")
        else:
            if not free_valid:
                print("Free workspace failed validation. Submissions may fail.")
                print(
                    "This may explain HTTP 500 errors if workspace lacks required data."
                )
            else:
                print("Free workspace validated successfully")
        print()

        results = []
        start_time = time.time()

        if parallel:
            # Submit all workflows in parallel
            print("Submitting workflows in PARALLEL...")

            with ThreadPoolExecutor(
                max_workers=min(premium_count + free_count, 8)
            ) as executor:
                # Submit premium workflows
                premium_futures = [
                    executor.submit(
                        self.submit_and_monitor_workflow,
                        "premium",
                        premium_workspace,
                        i + 1,
                        monitor_execution,
                    )
                    for i in range(premium_count)
                ]

                # Submit free workflows
                free_futures = [
                    executor.submit(
                        self.submit_and_monitor_workflow,
                        "free",
                        free_workspace,
                        i + 1,
                        monitor_execution,
                    )
                    for i in range(free_count)
                ]

                # Collect results
                all_futures = premium_futures + free_futures
                for future in as_completed(all_futures):
                    result = future.result()
                    results.append(result)

        else:
            # Submit workflows sequentially (mixed order)
            print("Submitting workflows SEQUENTIALLY (mixed order)...")

            # Interleave premium and free for fair testing
            max_count = max(premium_count, free_count)
            for i in range(max_count):
                if i < free_count:
                    result = self.submit_and_monitor_workflow(
                        "free", free_workspace, i + 1, monitor_execution
                    )
                    results.append(result)

                if i < premium_count:
                    result = self.submit_and_monitor_workflow(
                        "premium", premium_workspace, i + 1, monitor_execution
                    )
                    results.append(result)

        total_time = time.time() - start_time

        # Continuous monitoring and reporting until all workflows complete
        if monitor_execution:
            print("\n Starting continuous monitoring with live report updates...")
            print("Reports regenerated every 10 seconds until all workflows complete.")
            print("Use Ctrl+C to interrupt if needed.")
            self.continuous_monitoring_and_reporting(results, total_time)
        else:
            # Generate single report for submission-only mode
            timing_analysis = []
            self.generate_report(
                results, total_time, monitor_execution, timing_analysis
            )

        return results

    def decode_jwt_token(self, token: str, user_type: str):
        """Decode and analyze JWT token for debugging"""
        try:
            # JWT has 3 parts separated by dots: header.payload.signature
            parts = token.split(".")
            if len(parts) != 3:
                print(
                    f"Invalid JWT format for {user_type} user: "
                    f"{len(parts)} parts instead of 3"
                )
                return None

            # Decode header
            header_data = parts[0] + "=" * (4 - len(parts[0]) % 4)  # Add padding
            header = json.loads(base64.urlsafe_b64decode(header_data))

            # Decode payload
            payload_data = parts[1] + "=" * (4 - len(parts[1]) % 4)  # Add padding
            payload = json.loads(base64.urlsafe_b64decode(payload_data))

            print(f"JWT Analysis for {user_type.upper()} user:")
            print(f"Header: {json.dumps(header, indent=6)}")
            print("Payload key fields:")

            # Show important fields
            important_fields = [
                "iss",
                "aud",
                "sub",
                "email",
                "user_id",
                "iat",
                "exp",
                "auth_time",
            ]
            for field in important_fields:
                if field in payload:
                    if field in ["iat", "exp", "auth_time"]:
                        # Convert timestamp to readable date
                        readable_time = datetime.fromtimestamp(payload[field]).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        print(f"{field}: {payload[field]} ({readable_time})")
                    else:
                        print(f"{field}: {payload[field]}")

            # Check for custom claims that might be relevant
            custom_fields = set(payload.keys()) - set(important_fields)
            if custom_fields:
                print(f"Custom claims: {list(custom_fields)}")
                for field in custom_fields:
                    print(f"{field}: {payload[field]}")

            # Check token expiration
            if "exp" in payload:
                exp_time = payload["exp"]
                current_time = int(time.time())
                if exp_time < current_time:
                    print(
                        f"TOKEN EXPIRED! Expired {current_time - exp_time} seconds ago"
                    )
                else:
                    print(f"Token valid for {exp_time - current_time} more seconds")

            return {"header": header, "payload": payload}

        except Exception as e:
            print(f"Failed to decode JWT for {user_type} user: {e}")
            print(
                f"Token preview: {token[:50]}..{token[-20:] if len(token) > 70 else ''}"
            )
            return None

    def validate_workspace_setup(self, workspace_id: int, user_type: str) -> bool:
        """Validate that a workspace is properly set up for workflow submission"""
        try:
            data_dir = Path(os.environ.get("OPTINIST_DIR", "/tmp/studio"))
            input_dir = data_dir / "input" / str(workspace_id)
            output_dir = data_dir / "output" / str(workspace_id)

            print(f"Validating {user_type} workspace {workspace_id}:")

            # Check input directory
            if not input_dir.exists():
                print(f"Input directory missing: {input_dir}")
                return False

            input_files = list(input_dir.glob("*"))
            print(f"Input directory exists with {len(input_files)} files")
            if input_files:
                print(f"Sample files: {[f.name for f in input_files[:3]]}")

            # Check output directory exists (should be created)
            if not output_dir.exists():
                print(f"Output directory will be created: {output_dir}")
            else:
                print(f"Output directory exists: {output_dir}")

            return len(input_files) > 0

        except Exception as e:
            print(f"Workspace validation failed: {e}")
            return False

    def continuous_monitoring_and_reporting(
        self, results: List[Dict], initial_total_time: float
    ):
        """Continuously monitor workflows and regenerate reports until all complete"""
        successful_submissions = [
            r for r in results if r["submission_success"] and r["workflow_id"]
        ]

        if not successful_submissions:
            print("No successful submissions to monitor")
            timing_analysis = []
            self.generate_report(results, initial_total_time, True, timing_analysis)
            return

        check_interval = 10  # Check every 10 seconds
        report_count = 0

        while True:
            report_count += 1
            print("\n" + "=" * 50)
            print(
                f"REPORT UPDATE #{report_count} - {datetime.now().strftime('%H:%M:%S')}"
            )
            print("=" * 50)

            # Update workflow statuses by checking experiment.yaml files
            still_running_count = 0
            print(
                f"\nDEBUG: Checking status of {len(successful_submissions)} workflows.."
            )

            for result in successful_submissions:
                workspace_id = result["workspace_id"]
                workflow_id = result["workflow_id"]
                user_type = result["user_type"]

                # Check current status using experiment.yaml
                status_result = self.check_workflow_status_by_files(
                    workflow_id, workspace_id
                )

                print(
                    f"{user_type}:{workflow_id} - Status: {status_result['status']}, "
                    f"Completed: {status_result['completed']}, "
                    f"Reason: {status_result['reason']}"
                )

                if status_result["completed"]:
                    # Update the result with final status
                    if status_result["status"] == "success":
                        result["execution_success"] = True
                        result["overall_success"] = True
                        result["execution_error"] = None
                    else:
                        result["execution_success"] = False
                        result["overall_success"] = False
                        result["execution_error"] = status_result.get(
                            "reason", "Unknown error"
                        )
                else:
                    still_running_count += 1
                    # Keep as running status
                    result["execution_success"] = None  # Still running
                    result["overall_success"] = False  # Not complete yet

            print(f"DEBUG: still_running_count = {still_running_count}")

            # Generate fresh timing analysis
            timing_analysis = []
            for result in successful_submissions:
                # Use submission completion time (submission_time + submission_duration)
                #  for more accurate queue wait time
                submission_completion_time = (
                    result["submission_time"] + result["submission_duration"]
                )
                timing_data = self.analyze_experiment_timing(
                    result["workspace_id"],
                    result["workflow_id"],
                    submission_completion_time,
                )
                if timing_data:
                    timing_data["user_type"] = result["user_type"]
                    timing_analysis.append(timing_data)

            # Generate updated report
            self.generate_report(results, initial_total_time, True, timing_analysis)

            # Check for still running workflows from timing analysis
            still_running_from_timing = []
            if timing_analysis:
                for timing in timing_analysis:
                    if (
                        timing["workflow_success"] == "running"
                        or timing["overall_duration"] is None
                        or any(
                            node["node_completion_time"] is None
                            for node in timing.get("node_timings", [])
                        )
                    ):
                        still_running_from_timing.append(timing["workflow_id"])

            print("\n DEBUG COMPARISON:")
            print(f"still_running_count (file check): {still_running_count}")
            print(f"still_running_from_timing: {len(still_running_from_timing)}")

            # Use timing analysis from experiment.yaml directly
            if len(still_running_from_timing) == 0:
                print(
                    f"\nAll workflows completed! Final report generated"
                    f"after {report_count} updates."
                )
                break
            else:
                print(
                    f"\n{len(still_running_from_timing)} workflows still running "
                    f"(from timing analysis). Next update in {check_interval} seconds"
                )
                time.sleep(check_interval)

    def generate_report(
        self,
        results: List[Dict],
        total_time: float,
        monitored_execution: bool = True,
        timing_analysis: List[Dict] = None,
    ):
        """Generate a report of the test results"""

        submission_successful = [r for r in results if r["submission_success"]]
        submission_failed = [r for r in results if not r["submission_success"]]

        overall_successful = [r for r in results if r["overall_success"]]
        overall_failed = [r for r in results if not r["overall_success"]]

        premium_results = [r for r in results if r["user_type"] == "premium"]
        free_results = [r for r in results if r["user_type"] == "free"]

        premium_successful = [r for r in premium_results if r["overall_success"]]
        free_successful = [r for r in free_results if r["overall_success"]]

        print()
        print("=" * 50)
        print("PRIORITY QUEUE API TEST RESULTS")
        print("=" * 50)

        # Check if this is a live update
        still_running_from_timing = []
        if timing_analysis:
            print(f"DEBUG: Timing analysis check for {len(timing_analysis)} workflows:")
            for timing in timing_analysis:
                workflow_id = timing["workflow_id"]
                workflow_success = timing["workflow_success"]
                overall_duration = timing["overall_duration"]
                incomplete_nodes = [
                    node
                    for node in timing.get("node_timings", [])
                    if node["node_completion_time"] is None
                ]

                is_running = (
                    workflow_success == "running"
                    or overall_duration is None
                    or len(incomplete_nodes) > 0
                )

                print(
                    f"{workflow_id}: success={workflow_success}, "
                    f"duration={overall_duration}, "
                    f"incomplete_nodes={len(incomplete_nodes)}, "
                    f"is_running={is_running}"
                )

                if is_running:
                    still_running_from_timing.append(workflow_id)

        print(f"DEBUG: still_running_from_timing = {still_running_from_timing}")

        if still_running_from_timing:
            print("LIVE UPDATE - Some workflows still running")
        else:
            print("FINAL RESULTS - All workflows completed")
        print()

        # Summary
        print("SUBMISSION SUMMARY:")
        print(f"Total workflows submitted: {len(results)}")
        print(f"Successful submissions: {len(submission_successful)}")
        print(f"Failed submissions: {len(submission_failed)}")
        if submission_successful:
            print(
                f"Submission success rate: "
                f"{len(submission_successful)/len(results)*100:.1f}%"
            )
        print()

        if monitored_execution:
            print("EXECUTION SUMMARY:")
            print(f"Workflows completed successfully: {len(overall_successful)}")
            print(f"Workflows failed during execution: {len(overall_failed)}")
            print(
                f"Overall success rate: {len(overall_successful)/len(results)*100:.1f}%"
            )
            print()

        print(
            f"Premium workflows: {len(premium_results)} submitted, "
            f"{len(premium_successful)} successful"
        )
        print(
            f"Free workflows: {len(free_results)} submitted, "
            f"{len(free_successful)} successful"
        )
        print()

        # Timing analysis
        if submission_successful:
            avg_submission_time = sum(
                r["submission_duration"] for r in submission_successful
            ) / len(submission_successful)
            print("TIMING ANALYSIS:")
            print(f"Average submission time: {avg_submission_time:.3f}s")
            if monitored_execution and overall_successful:
                successful_with_exec_time = [
                    r for r in overall_successful if r["execution_time"] is not None
                ]
                if successful_with_exec_time:
                    avg_execution_time = sum(
                        r["execution_time"] for r in successful_with_exec_time
                    ) / len(successful_with_exec_time)
                    print(f"Average execution time: {avg_execution_time:.2f}s")
            print(f"Total test duration: {total_time:.2f}s")

            # Sort by submission order
            submission_successful.sort(key=lambda x: x["submission_time"])
            print("Submission order:")
            for i, result in enumerate(submission_successful, 1):
                exec_status = ""
                if monitored_execution:
                    if result["execution_success"]:
                        exec_status = f"({result['execution_time']:.1f}s exec)"
                    elif result["execution_success"] is False:
                        exec_status = "(failed)"
                    else:
                        exec_status = "(not monitored)"

                print(
                    f"{i}. {result['user_type'].upper()} #{result['workflow_num']} - "
                    f"ID: {result['workflow_id']} "
                    f"({result['submission_duration']:.3f}s sub{exec_status})"
                )
        print()

        # Error analysis
        if submission_failed:
            print("SUBMISSION FAILURES:")
            for result in submission_failed:
                print(
                    f"{result['user_type'].upper()} #{result['workflow_num']}: "
                    f"{result['submission_error']}"
                )

            # Add guidance for common HTTP 500 errors
            http_500_count = sum(
                1 for r in submission_failed if r.get("status_code") == 500
            )
            if http_500_count > 0:
                print(f"\n {http_500_count} workflows failed with HTTP 500 errors.")
            print()

        if monitored_execution:
            execution_failed = [
                r
                for r in results
                if r["submission_success"] and not r["execution_success"]
            ]
            if execution_failed:
                print("EXECUTION FAILURES:")
                for result in execution_failed:
                    print(
                        f"{result['user_type'].upper()} #{result['workflow_num']} "
                        f"({result['workflow_id']}):"
                    )
                    if result["execution_error"]:
                        # Show detailed error log content including error.log
                        error_lines = result["execution_error"].split("\n")
                        for line in error_lines[:5]:  # Show first 5 lines of error
                            if line.strip():
                                print(f"{line.strip()}")
                        if len(error_lines) > 5:
                            print(f"... and {len(error_lines) - 5} more lines")
                    else:
                        print("No detailed error information available")
                print()

        # Successful workflow IDs and outputs
        if overall_successful:
            print("SUCCESSFUL WORKFLOW IDs:")
            for result in overall_successful:
                print(f"{result['user_type'].upper()}: {result['workflow_id']}")
                if result.get("output_files"):
                    for output_file in result["output_files"][
                        :3
                    ]:  # Show first 3 output files
                        print(f"{output_file}")
                    if len(result["output_files"]) > 3:
                        print(f"... and {len(result['output_files']) - 3} more files")
            print()

        # Detailed timing analysis
        if timing_analysis:
            print("DETAILED TIMING ANALYSIS:")
            print("=" * 50)

            # Separate premium and free timing data
            premium_timings = [
                t for t in timing_analysis if t["user_type"] == "premium"
            ]
            free_timings = [t for t in timing_analysis if t["user_type"] == "free"]

            if premium_timings and free_timings:
                # Calculate averages for comparison
                def calc_avg(timings, field):
                    values = [t[field] for t in timings if t[field] is not None]
                    return sum(values) / len(values) if values else 0

                print("PRIORITY QUEUE EFFECTIVENESS COMPARISON:")
                print(
                    f"{'Metric':<30} {'Premium Avg':<15} {'Free Avg':<15} "
                    f"{'Premium Advantage':<20}"
                )
                print("-" * 50)

                # Queue wait time comparison
                prem_queue_wait = calc_avg(premium_timings, "queue_wait_time")
                free_queue_wait = calc_avg(free_timings, "queue_wait_time")
                advantage = (
                    f"{prem_queue_wait - free_queue_wait:.2f}s faster"
                    if prem_queue_wait < free_queue_wait
                    else f"{free_queue_wait - prem_queue_wait:.2f}s slower"
                )
                print(
                    f"{'Queue Wait Time':<30} {prem_queue_wait:<15.2f} "
                    f"{free_queue_wait:<15.2f} {advantage:<20}"
                )

                # Overall duration comparison
                prem_duration = calc_avg(premium_timings, "overall_duration")
                free_duration = calc_avg(free_timings, "overall_duration")
                advantage = (
                    f"{prem_duration - free_duration:.2f}s faster"
                    if prem_duration < free_duration
                    else f"{free_duration - prem_duration:.2f}s slower"
                )
                print(
                    f"{'Overall Duration':<30} {prem_duration:<15.2f} "
                    f"{free_duration:<15.2f} {advantage:<20}"
                )

                # Average node wait time comparison
                prem_node_waits = [
                    node["node_wait_time"]
                    for t in premium_timings
                    for node in t["node_timings"]
                    if node["node_wait_time"] is not None
                ]
                free_node_waits = [
                    node["node_wait_time"]
                    for t in free_timings
                    for node in t["node_timings"]
                    if node["node_wait_time"] is not None
                ]
                prem_avg_node_wait = (
                    sum(prem_node_waits) / len(prem_node_waits)
                    if prem_node_waits
                    else 0
                )
                free_avg_node_wait = (
                    sum(free_node_waits) / len(free_node_waits)
                    if free_node_waits
                    else 0
                )
                advantage = (
                    f"{prem_avg_node_wait - free_avg_node_wait:.2f}s faster"
                    if prem_avg_node_wait < free_avg_node_wait
                    else f"{free_avg_node_wait - prem_avg_node_wait:.2f}s slower"
                )
                print(
                    f"{'Avg Node Wait Time':<30} {prem_avg_node_wait:<15.2f} "
                    f"{free_avg_node_wait:<15.2f} {advantage:<20}"
                )

                # Average node completion time comparison
                prem_node_completions = [
                    node["node_completion_time"]
                    for t in premium_timings
                    for node in t["node_timings"]
                    if node["node_completion_time"] is not None
                ]
                free_node_completions = [
                    node["node_completion_time"]
                    for t in free_timings
                    for node in t["node_timings"]
                    if node["node_completion_time"] is not None
                ]
                prem_avg_node_completion = (
                    sum(prem_node_completions) / len(prem_node_completions)
                    if prem_node_completions
                    else 0
                )
                free_avg_node_completion = (
                    sum(free_node_completions) / len(free_node_completions)
                    if free_node_completions
                    else 0
                )
                advantage = (
                    f"{prem_avg_node_completion - free_avg_node_completion:.2f}s faster"
                    if prem_avg_node_completion < free_avg_node_completion
                    else f"{free_avg_node_completion - prem_avg_node_completion:.2f}s "
                    f"slower"
                )
                print(
                    f"{'Avg Node Completion Time':<30}"
                    f"{prem_avg_node_completion:<15.2f} "
                    f"{free_avg_node_completion:<15.2f} {advantage:<20}"
                )

                print()

            # Detailed per-workflow breakdown
            print("PER-WORKFLOW TIMING BREAKDOWN:")
            for timing in timing_analysis:
                print(
                    f"\\n{timing['user_type'].upper()} Workflow: "
                    f"{timing['workflow_id']}"
                )
                print(f"Queue Wait Time: {timing['queue_wait_time']:.2f}s")
                print(
                    f"Overall Duration: {timing['overall_duration']:.2f}s"
                    if timing["overall_duration"]
                    else "Overall Duration: Still running"
                )
                print(f"Success: {timing['workflow_success']}")
                print(
                    f"Nodes: {timing['successful_nodes']}/{timing['total_nodes']} "
                    f"successful"
                )

                # Show top 3 slowest nodes
                if timing["node_timings"]:
                    slow_nodes = sorted(
                        timing["node_timings"],
                        key=lambda x: x["node_completion_time"] or 0,
                        reverse=True,
                    )[:3]
                    print("Slowest Nodes:")
                    for i, node in enumerate(slow_nodes, 1):
                        completion_time = (
                            f"{node['node_completion_time']:.2f}s"
                            if node["node_completion_time"]
                            else "Still running"
                        )
                        print(f"{i}. {node['node_name']}: {completion_time}")

            print()

        # Next steps - updated for continuous monitoring
        print("NEXT STEPS:")
        if monitored_execution:
            if still_running_from_timing:
                print("1. Workflows still running - report will auto-update")
                print(
                    f"Running workflows: {', '.join(still_running_from_timing[:3])}"
                    + (
                        f"and {len(still_running_from_timing)-3} more"
                        if len(still_running_from_timing) > 3
                        else ""
                    )
                )
                print("2. Next report update in 10 seconds")
                print("3. Use Ctrl+C to interrupt monitoring if needed")
            elif overall_successful:
                print("1. All workflows completed successfully!")
                print(
                    "2. Review execution times and priority queue effectiveness above"
                )
                print("3. Consider running with more workflows for better statistics")
            else:
                print("1. All workflows failed during execution")
                print("2. Check sample data availability in workspace directories")
                print("3. Review execution error logs above for specific issues")
                print("4. Verify Snakemake environment and dependencies")
        else:
            if submission_successful:
                print("1. Monitor workflow execution manually using the workflow IDs")
                print("2. Check Snakemake execution order and timing")
                print("3. Look for priority-related log messages")
                print("4. Re-run with --monitor-execution to get execution status")
            else:
                print("1. Fix workflow submission issues first")
                print("2. Ensure JWT tokens are valid and workspaces exist")

        print()
        print("=" * 50)

        # Save detailed results - reuse same filename for continuous updates
        if not self.report_filename:
            self.report_filename = (
                f"simple_priority_test_results_{int(time.time())}.json"
            )
        results_file = self.report_filename
        with open(results_file, "w") as f:
            json.dump(
                {
                    "test_summary": {
                        "total_workflows": len(results),
                        "successful_submissions": len(submission_successful),
                        "failed_submissions": len(submission_failed),
                        "successful_executions": len(overall_successful)
                        if monitored_execution
                        else None,
                        "failed_executions": len(overall_failed)
                        if monitored_execution
                        else None,
                        "submission_success_rate": len(submission_successful)
                        / len(results)
                        * 100
                        if results
                        else 0,
                        "overall_success_rate": len(overall_successful)
                        / len(results)
                        * 100
                        if results
                        else 0,
                        "total_time": total_time,
                        "average_submission_time": sum(
                            r["submission_duration"] for r in submission_successful
                        )
                        / len(submission_successful)
                        if submission_successful
                        else 0,
                        "monitored_execution": monitored_execution,
                    },
                    "results": results,
                    "timing_analysis": timing_analysis if timing_analysis else [],
                },
                f,
                indent=2,
            )

        print(f"Detailed results saved to: {results_file}")

        # Return success rate (for continuous monitoring,
        # this gets called multiple times)
        return len(overall_successful) / len(results) if results else 0

    def analyze_experiment_timing(
        self, workspace_id: int, workflow_id: str, submission_time: float
    ) -> Optional[Dict]:
        """Analyze experiment.yaml for detailed timing metrics"""
        try:
            workflow_dir = Path(f"/tmp/studio/output/{workspace_id}/{workflow_id}")
            experiment_file = workflow_dir / "experiment.yaml"

            if not experiment_file.exists():
                return None

            with open(experiment_file, "r") as f:
                experiment_data = yaml.safe_load(f)

            # Parse workflow-level timing
            workflow_started_at = experiment_data.get("started_at")
            workflow_finished_at = experiment_data.get("finished_at")
            workflow_success = experiment_data.get("success")

            if not workflow_started_at:
                return None

            # Convert datetime strings to timestamps for calculation
            def parse_datetime(dt_str):
                try:
                    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception:
                    return None

            workflow_start_ts = parse_datetime(workflow_started_at)
            workflow_finish_ts = (
                parse_datetime(workflow_finished_at) if workflow_finished_at else None
            )

            if not workflow_start_ts:
                return None

            # Calculate key metrics
            # Use max(0, ...) to avoid negative queue wait times
            # due to timing precision or clock sync issues
            queue_wait_time = max(0.0, workflow_start_ts - submission_time)
            overall_duration = (
                workflow_finish_ts - submission_time if workflow_finish_ts else None
            )

            # Analyze individual nodes/functions
            node_timings = []
            functions = experiment_data.get("function", {})

            for func_id, func_data in functions.items():
                node_started_at = func_data.get("started_at")
                node_finished_at = func_data.get("finished_at")
                node_name = func_data.get("name", func_id)
                node_success = func_data.get("success")

                # Include all nodes, not just those with started_at
                # For nodes without started_at, use workflow start time as fallback
                if node_started_at:
                    node_start_ts = parse_datetime(node_started_at)
                else:
                    # Use workflow start time for nodes without explicit started_at
                    node_start_ts = workflow_start_ts
                    node_started_at = workflow_started_at

                node_finish_ts = (
                    parse_datetime(node_finished_at) if node_finished_at else None
                )

                if node_start_ts:
                    # Use max(0, ...) to avoid negative wait times
                    # due to timing precision or clock sync issues
                    node_wait_time = max(0.0, node_start_ts - submission_time)
                    node_completion_time = (
                        node_finish_ts - node_start_ts if node_finish_ts else None
                    )

                    node_timings.append(
                        {
                            "node_id": func_id,
                            "node_name": node_name,
                            "node_wait_time": node_wait_time,
                            "node_completion_time": node_completion_time,
                            "node_success": node_success,
                            "started_at": node_started_at,
                            "finished_at": node_finished_at,
                        }
                    )

            return {
                "workflow_id": workflow_id,
                "workspace_id": workspace_id,
                "submission_time": submission_time,
                "workflow_started_at": workflow_started_at,
                "workflow_finished_at": workflow_finished_at,
                "workflow_success": workflow_success,
                "queue_wait_time": queue_wait_time,
                "overall_duration": overall_duration,
                "node_timings": node_timings,
                "total_nodes": len(node_timings),
                "successful_nodes": len(
                    [n for n in node_timings if n["node_success"] == "success"]
                ),
            }

        except Exception as e:
            print(f"Could not analyze experiment timing for {workflow_id}: {e}")
            return None

    def cleanup(self):
        """Clean up test environment variables"""
        import os

        if "SKIP_STORAGE_CHECKS" in os.environ:
            del os.environ["SKIP_STORAGE_CHECKS"]


def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive Priority Queue API Test"
    )

    # Test configuration
    parser.add_argument(
        "--premium-count",
        type=int,
        default=3,
        help="Number of premium workflows to submit",
    )
    parser.add_argument(
        "--free-count", type=int, default=3, help="Number of free workflows to submit"
    )
    parser.add_argument(
        "--premium-workspace",
        type=int,
        help="Premium user workspace ID (auto-created if not specified)",
    )
    parser.add_argument(
        "--free-workspace",
        type=int,
        help="Free user workspace ID (auto-created if not specified)",
    )

    # Environment configuration
    parser.add_argument(
        "--environment",
        choices=["local", "cloud"],
        default="local",
        help="Test environment (local or cloud)",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8002", help="Base URL for API requests"
    )
    parser.add_argument(
        "--api-url", help="API URL (alias for --base-url, " "for compatibility)"
    )

    # Execution options
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout for workflow execution monitoring "
        "(seconds, default: 30 minutes)",
    )
    parser.add_argument(
        "--submission-only",
        action="store_true",
        help="Only test API submission without waiting " "for execution (fast test)",
    )

    # Setup options
    parser.add_argument(
        "--skip-token-gen",
        action="store_true",
        help="Skip JWT token generation (use existing tokens.json)",
    )
    parser.add_argument(
        "--test-free-only",
        action="store_true",
        help="Test only free workspace workflows " "(no premium) to isolate issues",
    )
    parser.add_argument(
        "--debug-tokens",
        action="store_true",
        help="Enable JWT token debugging output",
    )

    args = parser.parse_args()

    # Handle API URL compatibility
    if args.api_url:
        args.base_url = args.api_url

    # Set environment based on URL if not explicitly specified
    if args.environment == "local" and "localhost" not in args.base_url:
        args.environment = "cloud"
        print(f"🌐 Detected cloud environment from URL: {args.base_url}")

    # Handle free-only testing mode
    if args.test_free_only:
        print("FREE WORKSPACE ISOLATION TEST MODE")
        print("=" * 50)
        print(
            "This mode tests ONLY free workspace workflows to "
            "isolate potential issues."
        )
        print(
            "No premium workflows will be submitted to avoid "
            "priority queue interference."
        )
        args.premium_count = 0  # Override premium count to 0
    else:
        print("Comprehensive Priority Queue Test Runner")
        print("=" * 50)

    print(f"Environment: {args.environment}")
    print(f"Base URL: {args.base_url}")
    print(f"Premium workflows: {args.premium_count}")
    print(f"Free workflows: {args.free_count}")
    if args.test_free_only:
        print("MODE: Free workspace isolation test (no premium competition)")
    print()

    try:
        tester = SimplePriorityQueueTester(
            base_url=args.base_url,
            setup_sample_data=True,
            ensure_env=True,
            environment=args.environment,
            create_workspaces=not hasattr(args, "premium_workspace")
            or args.premium_workspace is None,
            skip_token_generation=args.skip_token_gen,
            debug_tokens=args.debug_tokens,
        )

        # Update timeout in the tester if monitoring is enabled
        tester.default_timeout = args.timeout

        # Determine monitoring mode
        monitor_execution = not args.submission_only

        # Default to parallel submission
        parallel = True

        results = tester.run_priority_test(
            premium_count=args.premium_count,
            free_count=args.free_count,
            premium_workspace=args.premium_workspace,
            free_workspace=args.free_workspace,
            parallel=parallel,
            monitor_execution=monitor_execution,
        )

        # Exit with success based on overall success
        successful_count = len([r for r in results if r["overall_success"]])
        success_rate = successful_count / len(results) if results else 0

        if success_rate >= 0.5:
            print(
                f"Test completed successfully "
                f"({success_rate:.1%} overall success rate)"
            )
            tester.cleanup()
            exit(0)
        else:
            print(
                f"Test completed with issues "
                f"({success_rate:.1%} overall success rate)"
            )
            tester.cleanup()
            exit(1)

    except Exception as e:
        print(f"Test failed: {e}")
        import traceback

        traceback.print_exc()
        # Clean up even if test failed
        try:
            tester.cleanup()
        except Exception as e:
            print(f"Cleanup failed: {e}")
        exit(2)


if __name__ == "__main__":
    main()
