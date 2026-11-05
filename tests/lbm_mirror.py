"""A numpy mirror of the browser preview's lattice-Boltzmann scheme (windlab/app/static/preview.js).

The dev container has no JavaScript engine, so the physics of the preview is checked here with
the same equations: D2Q9, BGK collision, Smagorinsky eddy viscosity, halfway bounce-back on the
part, freestream in / top / bottom, zero-gradient out, momentum-exchange forces. Keep the
constants equal to the JS ones (tests/test_preview.py compares them with the source).
"""

from __future__ import annotations

import numpy as np

U_IN = 0.08
TAU0 = 0.51
CS = 0.1
EX = np.array([0, 1, 0, -1, 0, 1, -1, -1, 1])
EY = np.array([0, 0, 1, 0, -1, 1, 1, -1, -1])
WT = np.array([4 / 9] + [1 / 9] * 4 + [1 / 36] * 4)
OPP = np.array([0, 3, 4, 1, 2, 7, 8, 5, 6])


def equilibrium(rho, ux, uy):
    usq = 1.5 * (ux * ux + uy * uy)
    cu = 3.0 * (EX[:, None, None] * ux + EY[:, None, None] * uy)
    return WT[:, None, None] * rho * (1 + cu + 0.5 * cu * cu - usq)


class Lattice:
    """f[k, j, i]: direction k, row j (y, up), column i (x, with the wind)."""

    def __init__(self, nx: int, ny: int, mask: np.ndarray):
        self.nx, self.ny = nx, ny
        self.mask = mask.astype(bool).reshape(ny, nx)
        self.f = equilibrium(np.ones((ny, nx)), np.full((ny, nx), U_IN), np.zeros((ny, nx)))
        self.rho = np.ones((ny, nx))
        self.ux = np.full((ny, nx), U_IN)
        self.uy = np.zeros((ny, nx))
        self.fx = self.fy = 0.0

    def step(self):
        f, m = self.f, self.mask
        ny, nx = self.ny, self.nx
        # pull streaming: f_k(x) <- f_k(x - e_k); bounce back where the source is solid
        fin = np.empty_like(f)
        for k in range(9):
            src = np.roll(np.roll(f[k], EY[k], axis=0), EX[k], axis=1)
            src_solid = np.roll(np.roll(m, EY[k], axis=0), EX[k], axis=1)
            fin[k] = np.where(src_solid, f[OPP[k]], src)
        rho = fin.sum(axis=0)
        ux = (EX[:, None, None] * fin).sum(axis=0) / rho
        uy = (EY[:, None, None] * fin).sum(axis=0) / rho
        feq = equilibrium(rho, ux, uy)
        neq = fin - feq
        pxx = (EX[:, None, None] ** 2 * neq).sum(axis=0)
        pyy = (EY[:, None, None] ** 2 * neq).sum(axis=0)
        pxy = (EX[:, None, None] * EY[:, None, None] * neq).sum(axis=0)
        q = np.sqrt(2 * (pxx**2 + pyy**2 + 2 * pxy**2))
        tau = 0.5 * (TAU0 + np.sqrt(TAU0**2 + 18 * CS**2 * q / rho))
        fpost = fin - neq / tau
        # boundaries
        eq_in = equilibrium(np.ones((ny, nx)), np.full((ny, nx), U_IN), np.zeros((ny, nx)))
        eq_0 = equilibrium(np.ones((ny, nx)), np.zeros((ny, nx)), np.zeros((ny, nx)))
        border = np.zeros((ny, nx), bool)
        border[0, :] = border[-1, :] = True
        border[:, 0] = True
        fpost = np.where(border[None], eq_in, fpost)
        fpost[:, :, -1] = f[:, :, -2]  # outflow copies the neighbour's post-collision values
        fpost = np.where(m[None], eq_0, fpost)
        # momentum exchange: links from fluid cells into solid neighbours
        fx = fy = 0.0
        fluid = ~m & ~border
        for k in range(1, 9):
            nb_solid = np.roll(np.roll(m, -EY[k], axis=0), -EX[k], axis=1)  # mask at x + e_k
            w = fluid & nb_solid
            s = fpost[k][w].sum()
            fx += 2 * EX[k] * s
            fy += 2 * EY[k] * s
        self.f, self.rho, self.ux, self.uy, self.fx, self.fy = fpost, rho, ux, uy, fx, fy
