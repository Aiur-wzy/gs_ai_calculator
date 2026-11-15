"""Registration shared by current-user setup and database initialization."""

FIRST_INSTALL_CONTRACT = "genshin-product-first-install-v1"
REGISTRATION_FILENAME = "installation.json"


class FirstInstallError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)
