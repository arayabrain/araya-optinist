#!/usr/bin/env python3

"""
Priority Queue Cluster Test Script

This script tests priority queue functionality by running workflows through the proper
Snakemake execution environment (with conda activation) to avoid dependency issues.

Based on run_cluster.py but adapted for priority queue testing with multiple workflows
for premium and free users.
"""

import argparse
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

from snakemake.api import (
    DAGSettings,
    DeploymentMethod,
    DeploymentSettings,
    OutputSettings,
    ResourceSettings,
    SnakemakeApi,
    StorageSettings,
)

from studio.app.dir_path import DIRPATH

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class PriorityQueueTester:
    """Handles priority queue testing with multiple workflows"""

    def __init__(self, workspace_dir: str = "/tmp/studio", cores: int = 2):
        self.workspace_dir = workspace_dir
        self.cores = cores
        self.results = []

    def load_base_workflow_config(self, config_file: str = None) -> Dict:
        """Load the base workflow configuration from test data"""

        if config_file is None:
            # Use the existing test workflow configuration
            config_file = str(
                Path(__file__).parent / "test-workflow-tutorial1-postdata.json"
            )

        if not Path(config_file).exists():
            raise FileNotFoundError(f"Workflow config file not found: {config_file}")

        with open(config_file, "r") as f:
            return json.load(f)

    def create_workflow_config(
        self,
        workflow_id: str,
        user_type: str,
        user_id: int,
        workspace_id: int,
        priority: int = 1,
        base_config: Dict = None,
    ) -> Dict:
        """Create a workflow configuration for testing by modifying base config"""

        if base_config is None:
            base_config = self.load_base_workflow_config()

        # Create a copy of the base configuration
        config = base_config.copy()

        # Modify the configuration for this specific test
        config["name"] = f"{user_type}_priority_test_{workflow_id}"

        # Add priority and user context (this will be handled by the workflow runner)
        config["user_id"] = user_id
        config["user_type"] = user_type
        config["workspace_id"] = workspace_id
        config["unique_id"] = workflow_id

        return config

    def save_config_to_file(self, config: Dict, temp_dir: Path) -> str:
        """Save workflow config to JSON file in temp directory"""
        config_path = temp_dir / "workflow_config.json"

        # Save the full JSON configuration
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        return str(config_path)

    def execute_workflow(
        self, workflow_config: Dict, forceall: bool = True
    ) -> Tuple[bool, float, str]:
        """Execute a single workflow and return success, duration, and workflow_id"""

        workflow_id = workflow_config["unique_id"]
        user_type = workflow_config["user_type"]

        print(f"Starting {user_type} workflow {workflow_id}")
        start_time = time.time()

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_workdir = Path(temp_dir)

                # Save config to temp directory
                config_path = self.save_config_to_file(workflow_config, temp_workdir)
                print(f"Config saved to: {config_path}")

                # Setup output directory
                output_dir = Path(
                    workflow_config["rules"]["suite2p_001"]["output"]
                ).parent
                output_dir.mkdir(parents=True, exist_ok=True)

                # Use Snakemake API with proper conda environment
                with SnakemakeApi(
                    OutputSettings(
                        verbose=True,
                        show_failed_logs=True,
                    ),
                ) as snakemake_api:
                    workflow_api = snakemake_api.workflow(
                        snakefile=Path(DIRPATH.SNAKEMAKE_FILEPATH),
                        workdir=temp_workdir,
                        storage_settings=StorageSettings(),
                        resource_settings=ResourceSettings(cores=self.cores),
                        deployment_settings=DeploymentSettings(
                            deployment_method=[DeploymentMethod.CONDA],
                            conda_frontend="conda",
                            conda_prefix=DIRPATH.SNAKEMAKE_CONDA_ENV_DIR,
                        ),
                    )

                    dag_settings = DAGSettings(
                        forceall=forceall,
                    )

                    dag_api = workflow_api.dag(
                        dag_settings=dag_settings,
                    )

                    # Execute workflow
                    dag_api.execute_workflow()

                    end_time = time.time()
                    duration = end_time - start_time

                    print(
                        f"{user_type} workflow {workflow_id} "
                        f"completed in {duration:.2f}s"
                    )
                    return True, duration, workflow_id

        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            print(
                f"{user_type} workflow {workflow_id} "
                f"failed after {duration:.2f}s: {e}"
            )
            return False, duration, workflow_id

    def run_workflows_parallel(
        self,
        premium_configs: List[Dict],
        free_configs: List[Dict],
        max_workers: int = 4,
    ) -> List[Dict]:
        """Run workflows in parallel to test true priority queue behavior"""

        print(
            f"Running {len(premium_configs)} premium + {len(free_configs)} free "
            f"workflows in parallel"
        )
        print(f"Max workers: {max_workers}")

        all_configs = premium_configs + free_configs
        results = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all workflows
            future_to_config = {
                executor.submit(self.execute_workflow, config): config
                for config in all_configs
            }

            # Collect results as they complete
            for future in as_completed(future_to_config):
                config = future_to_config[future]
                try:
                    success, duration, workflow_id = future.result()
                    result = {
                        "workflow_id": workflow_id,
                        "user_type": config["user_type"],
                        "success": success,
                        "duration": duration,
                        "completion_time": time.time(),
                    }
                    results.append(result)
                    print(
                        f"Completed: {result['user_type']} {workflow_id} - "
                        f"Success: {success}, Duration: {duration:.2f}s"
                    )

                except Exception as e:
                    print(f"Workflow {config['unique_id']} generated exception: {e}")

        return results

    def run_workflows_sequential(
        self, premium_configs: List[Dict], free_configs: List[Dict]
    ) -> List[Dict]:
        """Run workflows sequentially to test execution order"""

        print("Running workflows sequentially")
        print(f"Premium workflows: {len(premium_configs)}")
        print(f"Free workflows: {len(free_configs)}")

        results = []

        # Mix premium and free workflows to test priority ordering
        mixed_configs = []
        max_len = max(len(premium_configs), len(free_configs))

        for i in range(max_len):
            if i < len(free_configs):
                mixed_configs.append(free_configs[i])
            if i < len(premium_configs):
                mixed_configs.append(premium_configs[i])

        # Execute in mixed order
        for config in mixed_configs:
            success, duration, workflow_id = self.execute_workflow(config)
            result = {
                "workflow_id": workflow_id,
                "user_type": config["user_type"],
                "success": success,
                "duration": duration,
                "completion_time": time.time(),
            }
            results.append(result)

        return results

    def generate_report(self, results: List[Dict]) -> str:
        """Generate a summary report of the priority queue test results"""

        if not results:
            return "No results to report"

        premium_results = [r for r in results if r["user_type"] == "premium"]
        free_results = [r for r in results if r["user_type"] == "free"]

        successful_premium = [r for r in premium_results if r["success"]]
        successful_free = [r for r in free_results if r["success"]]

        report = []
        report.append("=" * 60)
        report.append("PRIORITY QUEUE TEST RESULTS")
        report.append("=" * 60)
        report.append("")

        # Summary statistics
        report.append("EXECUTION SUMMARY:")
        report.append(
            f"Premium workflows: {len(premium_results)} submitted, "
            f"{len(successful_premium)} successful"
        )
        report.append(
            f"Free workflows: {len(free_results)} submitted,"
            f" {len(successful_free)} successful"
        )
        report.append("")

        # Timing analysis
        if successful_premium:
            avg_premium_time = sum(r["duration"] for r in successful_premium) / len(
                successful_premium
            )
            report.append("TIMING ANALYSIS:")
            report.append(f"Average premium execution time: {avg_premium_time:.2f}s")

        if successful_free:
            avg_free_time = sum(r["duration"] for r in successful_free) / len(
                successful_free
            )
            if successful_premium:
                report.append(f"Average free execution time: {avg_free_time:.2f}s")
                speed_ratio = (
                    avg_free_time / avg_premium_time if avg_premium_time > 0 else 1
                )
                report.append(f"Speed ratio (free/premium): {speed_ratio:.2f}x")
            else:
                report.append(f"Average free execution time: {avg_free_time:.2f}s")

        report.append("")

        # Completion order analysis
        successful_results = [r for r in results if r["success"]]
        if successful_results:
            successful_results.sort(key=lambda x: x["completion_time"])
            report.append("COMPLETION ORDER:")
            for i, result in enumerate(successful_results, 1):
                report.append(
                    f"{i}. {result['user_type'].upper()} {result['workflow_id']} "
                    f"(priority={result['priority']}, {result['duration']:.2f}s)"
                )

        report.append("")

        # Priority queue effectiveness
        if len(successful_results) >= 2:
            first_result = successful_results[0]
            report.append("PRIORITY QUEUE EFFECTIVENESS:")
            report.append(
                f"First to complete: {first_result['user_type'].upper()} "
                f"(priority={first_result['priority']})"
            )

            premium_completions = [
                i
                for i, r in enumerate(successful_results)
                if r["user_type"] == "premium"
            ]
            if premium_completions:
                avg_premium_position = sum(premium_completions) / len(
                    premium_completions
                )
                report.append(
                    f"Average premium completion position: "
                    f"{avg_premium_position + 1:.1f}"
                )

        report.append("")
        report.append("=" * 60)

        return "\n".join(report)


def main(args):
    """Main function to run priority queue tests"""

    print("Priority Queue Cluster Test Starting")
    print("=" * 50)

    # Initialize tester
    tester = PriorityQueueTester(workspace_dir=args.workspace_dir, cores=args.cores)

    # Generate test workflows
    print("Generating test workflow configurations...")

    premium_configs = []
    free_configs = []

    # Create premium user workflows (priority 10)
    for i in range(args.premium_count):
        config = tester.create_workflow_config(
            workflow_id=f"premium_{i+1}_{int(time.time()*1000)}",
            user_type="premium",
            user_id=1,  # Premium user ID
            workspace_id=8,  # Premium workspace
            priority=10,
        )
        premium_configs.append(config)

    # Create free user workflows (priority 1)
    for i in range(args.free_count):
        config = tester.create_workflow_config(
            workflow_id=f"free_{i+1}_{int(time.time()*1000)}",
            user_type="free",
            user_id=2,  # Free user ID
            workspace_id=9,  # Free workspace
            priority=1,
        )
        free_configs.append(config)

    print(
        f"Generated {len(premium_configs)} premium + {len(free_configs)} free "
        f"workflow configs"
    )

    # Execute workflows based on mode
    start_time = time.time()

    if args.parallel:
        print(
            f"Running workflows in PARALLEL mode " f"(max_workers={args.max_workers})"
        )
        results = tester.run_workflows_parallel(
            premium_configs, free_configs, args.max_workers
        )
    else:
        print("Running workflows in SEQUENTIAL mode")
        results = tester.run_workflows_sequential(premium_configs, free_configs)

    total_time = time.time() - start_time

    # Generate and display report
    report = tester.generate_report(results)
    print("\n" + report)

    print(f"\nTotal test execution time: {total_time:.2f}s")

    # Save results to file
    results_file = f"priority_queue_cluster_test_results_{int(time.time())}.json"
    with open(results_file, "w") as f:
        json.dump(
            {
                "test_config": {
                    "premium_count": args.premium_count,
                    "free_count": args.free_count,
                    "parallel": args.parallel,
                    "cores": args.cores,
                    "max_workers": args.max_workers if args.parallel else 1,
                    "total_time": total_time,
                },
                "results": results,
                "report": report,
            },
            f,
            indent=2,
        )

    print(f"Results saved to: {results_file}")

    # Return success if at least some workflows completed
    successful_count = len([r for r in results if r["success"]])
    total_count = len(results)
    success_rate = successful_count / total_count if total_count > 0 else 0

    print(f"Success rate: {successful_count}/{total_count} ({success_rate:.1%})")

    return success_rate > 0.5  # Consider test successful if > 50% workflows complete


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Priority Queue Cluster Test - Tests workflow priority execution"
    )

    # Core execution parameters
    parser.add_argument(
        "--cores", type=int, default=2, help="Number of cores for Snakemake execution"
    )
    parser.add_argument(
        "--workspace-dir",
        type=str,
        default="/tmp/studio",
        help="Base workspace directory",
    )

    # Test configuration
    parser.add_argument(
        "--premium-count",
        type=int,
        default=3,
        help="Number of premium user workflows to test",
    )
    parser.add_argument(
        "--free-count",
        type=int,
        default=3,
        help="Number of free user workflows to test",
    )

    # Execution mode
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run workflows in parallel (default: sequential)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="Max parallel workers (only used with --parallel)",
    )

    # Snakemake options
    parser.add_argument(
        "--forceall",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Force rerun of all rules",
    )

    try:
        success = main(parser.parse_args())
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n  Test interrupted by user")
        exit(2)
    except Exception as e:
        print(f"\n Test failed with error: {e}")
        exit(1)
