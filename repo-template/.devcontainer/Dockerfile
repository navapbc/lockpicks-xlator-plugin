FROM ocaml/opam:ubuntu-24.04-ocaml-5.4

USER root
# ninja-build and libgmp-dev are required by catala's opam dependencies (conf-ninja, conf-gmp).
# pkg-config is required by conf-pkg-config.
# Opam and uv cannot be installed via apt at the required versions, so curl is used below.
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

# catala-format's opam build requires cargo (topiary -> conf-rust-2021).
# Ubuntu 24.04's apt ships ~1.75 which may be too old; install via rustup instead.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable

# Install catala, its language server, and formatter (OCaml compiler already present in base image).
# catala-lsp and catala-format are separate opam packages; installing together ensures a compatible set.
# PATH must include ~/.cargo/bin so opam can satisfy the conf-rust-2021 depext.
# --no-depexts: opam's depext check queries apt/dpkg for 'cargo', which rustup-installed cargo does
# not satisfy; cargo is still on PATH for the actual build via . ~/.cargo/env.
RUN . ~/.cargo/env && opam install -y --no-depexts catala.1.1.0 catala-lsp catala-format

# uv manages the Python version and venv. It is not available in apt.
RUN curl -fsSL https://astral.sh/uv/install.sh | sh

RUN curl -fsSL https://claude.ai/install.sh | bash

RUN echo 'eval $(opam env); export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
