#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/*
 * Finder does not attach stdin to an application bundle. This tiny native
 * launcher locates run.command relative to itself and asks Terminal to open
 * it. No developer-machine path is embedded in the executable.
 */
int main(void) {
    char executable_path[PATH_MAX];
    uint32_t size = sizeof(executable_path);
    if (_NSGetExecutablePath(executable_path, &size) != 0) {
        return 70;
    }

    char resolved_path[PATH_MAX];
    if (realpath(executable_path, resolved_path) == NULL) {
        return 71;
    }

    char *marker = strstr(resolved_path, "/Contents/MacOS/");
    if (marker == NULL) {
        return 72;
    }
    *marker = '\0';

    char command_path[PATH_MAX];
    int written = snprintf(
        command_path,
        sizeof(command_path),
        "%s/Contents/Resources/run.command",
        resolved_path
    );
    if (written < 0 || (size_t)written >= sizeof(command_path)) {
        return 73;
    }
    if (access(command_path, X_OK) != 0) {
        return errno ? errno : 74;
    }

    /* Release tests exercise path resolution without opening a GUI window. */
    if (getenv("BSTATION_LAUNCHER_DRY_RUN") != NULL) {
        puts(command_path);
        return 0;
    }

    execl(
        "/usr/bin/open",
        "open",
        "-b",
        "com.apple.Terminal",
        command_path,
        (char *)NULL
    );
    return errno ? errno : 75;
}
