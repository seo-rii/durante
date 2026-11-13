FROM debian:trixie-slim@sha256:28de0877c2189802884ccd20f15ee41c203573bd87bb6b883f5f46362d24c5c2
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 libcap2 util-linux xz-utils openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# The self-contained official build keeps builtins under the already exposed
# runtime tree; Debian's Node package externalizes them into /usr/share/nodejs.
ADD --checksum=sha256:cdeb8670a0c81f51b4dc4ac61919f9b1ad917b304bf8221eb939570663d240f8 \
    https://nodejs.org/dist/v22.23.5/node-v22.23.5-linux-x64.tar.xz /tmp/node.tar.xz
RUN mkdir /usr/lib/durante-node \
    && tar -xJf /tmp/node.tar.xz --strip-components=1 -C /usr/lib/durante-node \
    && ln -s /usr/lib/durante-node/bin/node /usr/bin/node \
    && rm /tmp/node.tar.xz

# Debian's JDK configuration links into /etc, which isolate does not expose.
# Keep the image's own default configuration inside the already exposed JDK;
# this does not add host mounts or weaken the production namespace profile.
RUN python3 - <<'PY'
from pathlib import Path
import shutil
root = Path('/usr/lib/jvm/java-21-openjdk-amd64')
assert (root / 'bin/java').is_file()
for path in sorted(root.rglob('*')):
    if path.is_symlink() and path.resolve().is_relative_to('/etc'):
        target = path.resolve(strict=True)
        temporary = path.with_name(path.name + '.image-copy')
        if target.is_dir():
            shutil.copytree(target, temporary, symlinks=False)
        else:
            shutil.copyfile(target, temporary)
        path.unlink()
        temporary.rename(path)
PY
