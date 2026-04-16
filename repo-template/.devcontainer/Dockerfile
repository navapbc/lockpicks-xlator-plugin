FROM ocaml/opam:ubuntu-24.04-ocaml-5.4

USER root
# ninja-build and libgmp-dev are required by catala's opam dependencies (conf-ninja, conf-gmp).
# pkg-config is required by conf-pkg-config.
# Opam, Rust, and uv cannot be installed via apt at the required versions for our other tools,
# so curl is used to install them below.
RUN apt-get update && apt-get install -y curl ninja-build libgmp-dev pkg-config && rm -rf /var/lib/apt/lists/*

# The ocaml/opam base image bundles an older opam (2.0.x). Ubuntu 24.04's apt
# also only has 2.1.x. We replace the binary with 2.5.0 from the official GitHub
# release. Installing to /usr/local/bin shadows the bundled binary via PATH
# without removing it.
RUN ARCH=$(uname -m | sed 's/aarch64/arm64/') \
    && curl -fsSL "https://github.com/ocaml/opam/releases/download/2.5.0/opam-2.5.0-${ARCH}-linux" \
    -o /usr/local/bin/opam \
    && chmod 755 /usr/local/bin/opam

USER opam

# Install catala (OCaml compiler already present in base image)
RUN opam install -y catala.1.1.0

# Rust 1.93 is required for catala-format. Ubuntu 24.04's apt ships ~1.75,
# so we install via rustup.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.93

# uv manages the Python version and venv. It is not available in apt.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN echo 'eval $(opam env)' >> ~/.bashrc \
    && echo 'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"' >> ~/.bashrc
