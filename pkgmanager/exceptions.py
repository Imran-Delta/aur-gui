"""
Exception hierarchy for pkgmanager. Every error the library can raise is a
subclass of PackageManagerError, so callers that just want to catch
"anything pkgmanager-related went wrong" can catch that one class.
"""


class PackageManagerError(Exception):
    """Base exception for all pkgmanager errors."""


class HelperNotFoundError(PackageManagerError):
    """Raised when a required helper binary is not in PATH."""


class NoHelperError(PackageManagerError):
    """Raised when no helper (not even pacman) is found."""


class AURHelperMissingError(PackageManagerError):
    """Raised when an AUR operation is attempted but no AUR helper is available."""


class CommandFailedError(PackageManagerError):
    """Raised when a subprocess returns a non-zero exit code."""

    def __init__(self, cmd, returncode, stdout, stderr):
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"Command {cmd} failed with code {returncode}")


class PermissionDeniedError(PackageManagerError):
    """Raised when permission escalation fails (e.g., pkexec/sudo error)."""
