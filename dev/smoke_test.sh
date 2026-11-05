#!/bin/bash
# Smoke test: runs the stock OpenFOAM motorBike tutorial (snappyHexMesh + simpleFoam,
# external aerodynamics) and reports timing and force coefficients.
source /usr/lib/openfoam/openfoam${OPENFOAM_VERSION}/etc/bashrc
set -e
RUN=/tmp/smoke_motorBike
rm -rf $RUN
cp -r $FOAM_TUTORIALS/incompressible/simpleFoam/motorBike $RUN
cd $RUN
echo "OpenFOAM: $WM_PROJECT_VERSION   arch: $(uname -m)   cores: $(nproc)"
START=$(date +%s)
./Allrun > allrun.out 2>&1 || { echo "Allrun FAILED"; tail -40 allrun.out; exit 1; }
END=$(date +%s)
echo "motorBike tutorial finished in $((END-START)) s"
grep -h "cells:" log.checkMesh* 2>/dev/null | head -2 || true
tail -3 log.simpleFoam 2>/dev/null || true
f=$(find postProcessing -name 'coefficient*.dat' | head -1)
[ -n "$f" ] && { echo "Final force coefficients ($f):"; grep '^#' "$f" | tail -1; tail -1 "$f"; }
echo "SMOKE TEST PASSED"
