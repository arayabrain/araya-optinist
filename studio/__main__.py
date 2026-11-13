from studio.app.common.core.logger import AppLogger
from studio.server import main

# Initialize the logger
logger = AppLogger.get_logger()

# run backend main module.
if __name__ == "__main__":
    logger.info("Starting Optinist application")
    main()
