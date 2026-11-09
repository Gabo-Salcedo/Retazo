"""Run yt-dlp with Windows certificates and an available JS runtime."""
import os
import shutil
import sys
import truststore

truststore.inject_into_ssl()

from yt_dlp import main

if __name__ == "__main__":
    # Prefer a normal installation; also support the runtime available on this PC.
    node = shutil.which("node")
    bundled_node = os.path.join(os.path.expanduser("~"), ".cache", "codex-runtimes",
                                "codex-primary-runtime", "dependencies", "node", "bin", "node.exe")
    if not node and os.path.isfile(bundled_node):
        node = bundled_node
    args = ["--encoding", "utf-8", "--no-playlist"]
    if node:
        args += ["--js-runtimes", "node:" + node]
    main(args + sys.argv[1:])
