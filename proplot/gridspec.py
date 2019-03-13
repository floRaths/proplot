#!/usr/bin/env python3
import numpy as np
import matplotlib.gridspec as mgridspec
import re
from .rcmod import rc
from .utils import _default, units, ic

# Generate custom GridSpec classes that override the GridSpecBase
# __setitem__ method a* nd the 'base' __init__ method
class FlexibleGridSpecBase(object):
    """
    Generalization of builtin `~matplotlib.gridspec.GridSpec` that allows for
    grids with **arbitrary spacing** between rows and columns of axes.

    Accomplishes this by actually drawing ``nrows*2 + 1`` and ``ncols*2 + 1``
    `~matplotlib.gridspec.GridSpecBase` rows and columns, setting
    `wspace` and `hspace` to 0, and masking out every other row/column
    of the `~matplotlib.gridspec.GridSpecBase`, so they act as "spaces".
    These "spaces" are allowed to vary in width using the builtin `wratios`
    and `hratios` keyword args.
    """
    def __init__(self, nrows, ncols, **kwargs):
        """
        Parameters
        ----------
        nrows, ncols : int
            Number of rows, columns on the subplot grid.
        wspace, hspace : float or list of float
            The horizontal, vertical spacing between columns, rows of
            subplots. Values are scaled relative to the height and width
            ratios. For example, ``wspace=0.1`` with the default ``wratios=1``
            yields a space 10% the width of the axes.

            If list, length length of ``wspace`` must be ``ncols-1``,
            and length of ``hspace`` must be ``nrows-1``.
        height_ratios, width_ratios : list of float
            Aliases for `hratios`, `wratios`.
        hratios, wratios : list of float
            Ratios for the width/height of columns/rows of subplots.
            For example, ``wratios=[1,2]`` specifes 2 columns of subplots,
            the second one twice as wide as the first.
        left, right, top, bottom : float or str
            Passed to `~matplotlib.gridspec.GridSpec`. Indicates width of "margins"
            surrounding the grid. If float, units are inches. If string,
            units are interpreted by `~proplot.utils.units`.

            Generally, these are used to set the "figure edges" around the
            region of subplots. If `~proplot.subplots.subplots` was called
            with ``tight=True`` (the default), these are ignored.
        """
        # Add these as attributes; want spaces_as_ratios to be
        # self-contained, so it can be invoked on already instantiated
        # gridspec (see 'update')
        self._nrows_visible = nrows
        self._ncols_visible = ncols
        self._nrows = nrows*2-1
        self._ncols = ncols*2-1
        wratios, hratios, kwargs = self.spaces_as_ratios(**kwargs)
        return super().__init__(self._nrows, self._ncols,
                hspace=0, wspace=0, # we implement these as invisible rows/columns
                width_ratios=wratios,
                height_ratios=hratios,
                **kwargs,
                )

    def __getitem__(self, key):
        # Magic obfuscation that renders rows and columns designated as
        # 'spaces' invisible. Note: key is tuple if multiple indices requested.
        def _normalize(key, size):
            if isinstance(key, slice):
                start, stop, _ = key.indices(size)
                if stop > start:
                    return start, stop - 1
            else:
                if key < 0:
                    key += size
                if 0 <= key < size:
                    return key, key
            raise IndexError(f"Invalid index: {key} with size {size}.")
        # SubplotSpec initialization figures out the row/column
        # geometry of these two numbers automatically
        nrows, ncols = self._nrows, self._ncols
        nrows_visible, ncols_visible = self._nrows_visible, self._ncols_visible
        if isinstance(key, tuple):
            try:
                k1, k2 = key
            except ValueError:
                raise ValueError('Unrecognized subplot spec "{key}".')
            num1, num2 = np.ravel_multi_index(
                [_normalize(k1, nrows_visible), _normalize(k2, ncols_visible)],
                (nrows, ncols),
                )
        else:
            num1, num2 = _normalize(key, nrows_visible * ncols_visible)
        # When you move to a new column that skips a 'hspace' and when you
        # move to a new row that skips a 'wspace' -- so, just multiply
        # the scalar indices by 2!
        def _adjust(n):
            if n<0:
                return 2*(n+1) - 1 # want -1 to stay -1, -2 becomes -3, etc.
            else:
                return n*2
        num1, num2 = _adjust(num1), _adjust(num2)
        return mgridspec.SubplotSpec(self, num1, num2)

    def spaces_as_ratios(self,
            hspace=None, wspace=None, # spacing between axes
            hratios=None, wratios=None,
            height_ratios=None, width_ratios=None,
            **kwargs):
        """
        For keyword arg usage, see `FlexibleGridSpecBase`.

        Returns
        -------
        wratios_final, hratios_final : list
            The final width and height ratios to be passed to
            `~matplotlib.gridspec.GridSpec` or
            `~matplotlib.gridspec.GridSpecFromSubplotSpec`.
        kwargs : dict
            Leftover keyword args, to be passed to
            `~matplotlib.gridspec.GridSpec` or
            `~matplotlib.gridspec.GridSpecFromSubplotSpec`.
        """
        # Parse flexible input
        nrows = self._nrows_visible
        ncols = self._ncols_visible
        hratios = np.atleast_1d(_default(height_ratios, hratios, 1))
        wratios = np.atleast_1d(_default(width_ratios,  wratios, 1))
        hspace = np.atleast_1d(_default(hspace, np.mean(hratios)*0.10)) # this is relative to axes
        wspace = np.atleast_1d(_default(wspace, np.mean(wratios)*0.10))
        if len(wspace)==1:
            wspace = np.repeat(wspace, (ncols-1,)) # note: may be length 0
        if len(hspace)==1:
            hspace = np.repeat(hspace, (nrows-1,))
        if len(wratios)==1:
            wratios = np.repeat(wratios, (ncols,))
        if len(hratios)==1:
            hratios = np.repeat(hratios, (nrows,))

        # Verify input ratios and spacings
        # Translate height/width spacings, implement as extra columns/rows
        if len(hratios) != nrows:
            raise ValueError(f'Got {nrows} rows, but {len(hratios)} hratios.')
        if len(wratios) != ncols:
            raise ValueError(f'Got {ncols} columns, but {len(wratios)} wratios.')
        if ncols>1 and len(wspace) != ncols-1:
            raise ValueError(f'Require {ncols-1} width spacings for {ncols} columns, got {len(wspace)}.')
        if nrows>1 and len(hspace) != nrows-1:
            raise ValueError(f'Require {nrows-1} height spacings for {nrows} rows, got {len(hspace)}.')

        # Assign spacing as ratios
        wratios_final = [None]*self._ncols
        wratios_final[::2] = [*wratios]
        if self._ncols>1:
            wratios_final[1::2] = [*wspace]
        hratios_final = [None]*self._nrows
        hratios_final[::2] = [*hratios]
        if self._nrows>1:
            hratios_final[1::2] = [*hspace]
        return wratios_final, hratios_final, kwargs # bring extra kwargs back

    def update(self, **gridspec_kw):
        """Updates the width, height ratios and spacing for subplot columns, rows."""
        # Handle special hspace/wspace arguments, and just set the simple
        # left/right/top/bottom attributes
        wratios, hratios, edges_kw = self.spaces_as_ratios(**gridspec_kw)
        self.set_width_ratios(wratios)
        self.set_height_ratios(hratios)
        edges_kw = {key:value for key,value in edges_kw.items()
            if key not in ('nrows','ncols')} # cannot be modified
        super().update(**edges_kw) # remaining kwargs should just be left/right/top/bottom

class FlexibleGridSpec(FlexibleGridSpecBase, mgridspec.GridSpec):
    """Dummy mixer class. See `FlexibleGridSpecBase`."""
    pass

class FlexibleGridSpecFromSubplotSpec(FlexibleGridSpecBase, mgridspec.GridSpecFromSubplotSpec):
    """Dummy mixer class. See `FlexibleGridSpecBase`."""
    pass

