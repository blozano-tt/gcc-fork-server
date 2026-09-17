// SPDX-License-Identifier: MIT
// Experimental, single-client GCC fork server. Fixed compiler options and paths.
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <sys/wait.h>
#include <unistd.h>

#include "gcc-plugin.h"
#include "plugin-version.h"

int plugin_is_GPL_compatible;

static void reply(const char* message) {
    const char* p = message;
    size_t left = __builtin_strlen(message);
    while (left) {
        const ssize_t n = write(STDOUT_FILENO, p, left);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) _exit(2);
        p += n;
        left -= n;
    }
}

int plugin_init(plugin_name_args*, plugin_gcc_version* version) {
    if (!plugin_default_version_check(version, &gcc_version)) return 1;

    // Fork in plugin_init, before this GCC opens the source/output files.
    // Each child returns into ordinary GCC, reads the latest source, and exits.
    // The parent never compiles or accumulates state from completed jobs.
    char response[128];
    std::snprintf(response, sizeof(response), "READY %ld\n", static_cast<long>(getpid()));
    reply(response);
    for (;;) {
        char command;
        ssize_t n;
        do { n = read(STDIN_FILENO, &command, 1); } while (n < 0 && errno == EINTR);
        if (n == 0 || (n == 1 && command == 'Q')) _exit(0);
        if (n != 1 || command != 'C') _exit(2);
        std::fflush(nullptr);
        const pid_t child = fork();
        if (child == 0) {
            close(STDIN_FILENO);
            return 0;  // Continue compilation in the child. There is no exec().
        }
        if (child < 0) _exit(2);
        int status;
        pid_t waited;
        do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
        if (waited != child) _exit(2);
        const int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
        std::snprintf(response, sizeof(response), "DONE %ld %d\n", static_cast<long>(child), code);
        reply(response);
    }
}
