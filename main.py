# run backend main module.
from studio.app.common.core.logger import AppLogger

logger = AppLogger.get_logger()

if __name__ == "__main__":
    from studio.server import main

    logger.info("Accessing main.py")
    main(develop_mode=True)
