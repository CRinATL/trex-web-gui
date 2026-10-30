#!/bin/bash
# Downloads the guest image and TRex v3.08 and verifies both, failing closed: the image against Ubuntu's SHA256SUMS
# (itself verified with gpgv against the ubuntu-cloudimage keyring), TRex against the committed v3.08.tar.gz.sha256
# (no upstream checksum exists). Any failed download, signature or hash exits non-zero. The guest comes from
# TREX_VM_GUEST (vm/guests.sh; default jammy): the image goes to $TREX_VM_DL (jammy: /var/tmp/trex-vm/dl), the
# tarball to $TREX_VM_DL_TREX (jammy: the same dir; other guests share one), and a full run skips a tarball that
# already verifies.
# Usage: download.sh [--verify-only]   (--verify-only re-checks files already downloaded)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/guests.sh" || exit 2
IMG=$TREX_VM_IMAGE
REL=$TREX_VM_IMAGE_URL
DL=$TREX_VM_DL
KEYRING=/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg
TAR=$TREX_VM_DL_TREX/v3.08.tar.gz
die() { echo "FAILED: $*" >&2; exit 1; }
tar_ok() { (cd "$TREX_VM_DL_TREX" && sha256sum -c "$HERE/v3.08.tar.gz.sha256") >/dev/null 2>&1; }
if [ "$TREX_VM_CODENAME" != jammy ]; then   # dirs under world-writable /var/tmp: checked, then made mode 700
  for d in "$TREX_VM_DL" "$TREX_VM_DL_TREX"; do
    if "$HERE/build.sh" --is-protected "$d"; then echo "FAILED: $d is a protected dir" >&2; exit 2; fi
    if why=$(trex_dir_unsafe "$d"); then echo "FAILED: $why" >&2; exit 2; fi
  done
  if [ "${1:-}" = "--verify-only" ]; then   # creates nothing
    [ -d "$DL" ] || die "nothing downloaded in $DL"
  else
    trex_mkdir700 "$TREX_VM_DL" && trex_mkdir700 "$TREX_VM_DL_TREX" || die "cannot create the download dirs"
  fi
fi
cd "$DL"

if [ "${1:-}" != "--verify-only" ]; then
  t0=$(date +%s)
  curl -sSL --fail -o SHA256SUMS "$REL/SHA256SUMS" || die "download SHA256SUMS"
  curl -sSL --fail -o SHA256SUMS.gpg "$REL/SHA256SUMS.gpg" || die "download SHA256SUMS.gpg"
  curl -sSL --fail -o "$IMG" "$REL/$IMG" 2>img.err & p1=$!
  r2=0; p2=""
  if [ "$TREX_VM_CODENAME" != jammy ] && tar_ok; then
    echo "v3.08.tar.gz already verified in $TREX_VM_DL_TREX: not downloaded"
  else
    curl -sSL --fail --cacert "$TREX_VM_TLS" -o "$TAR" \
      https://trex-tgn.cisco.com/trex/release/v3.08.tar.gz 2>trex.err & p2=$!
  fi
  r1=0; wait $p1 || r1=$?; if [ -n "$p2" ]; then wait $p2 || r2=$?; fi
  echo "curl rc img=$r1 trex=$r2 in $(( $(date +%s)-t0 ))s"
  [ "$r1" -eq 0 ] || die "image download (see $DL/img.err)"
  [ "$r2" -eq 0 ] || die "TRex download (see $DL/trex.err)"
fi

[ -f "$KEYRING" ] || die "missing $KEYRING (apt install ubuntu-cloudimage-keyring)"
gpgv --keyring "$KEYRING" SHA256SUMS.gpg SHA256SUMS || die "SHA256SUMS signature"
line=$(grep " \*$IMG\$" SHA256SUMS) || die "$IMG not listed in SHA256SUMS"
sed 's/ \*/  /' <<<"$line" | sha256sum -c - || die "$IMG hash"
(cd "$TREX_VM_DL_TREX" && sha256sum -c "$HERE/v3.08.tar.gz.sha256") || die "v3.08.tar.gz hash"   # never rewrites the committed hash
if [ "$TREX_VM_DL" = "$TREX_VM_DL_TREX" ]; then ls -l "$IMG" v3.08.tar.gz; else ls -l "$IMG" "$TAR"; fi
