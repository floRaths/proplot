#!/usr/bin/env python3
"""
The starting point for creating custom ProPlot figures and axes.
The `subplots` command is all you'll need to directly use here;
it returns a figure instance and list of axes.

Note that
instead of separating features into their own functions (e.g.
a `generate_panel` function), we specify them right when the
figure is declared.

The reason for this approach is we want to build a
*static figure "scaffolding"* before plotting anything. This
way, ProPlot can hold the axes aspect ratios, panel widths, colorbar
widths, and inter-axes spaces **fixed** (note this was really hard
to do and is **really darn cool**!).

See `~Figure.smart_tight_layout` for details.
"""
import os
import re
import numpy as np
# Local modules, projection sand formatters and stuff
from .rcmod import rc
from .utils import _default, _timer, _counter, units, journals, ic
from . import axistools, gridspec, projs, axes
import functools
import warnings
import matplotlib.pyplot as plt
import matplotlib.scale as mscale
import matplotlib.figure as mfigure
import matplotlib.transforms as mtransforms
# For panel names
_aliases = {
    'bpanel': 'bottompanel',
    'rpanel': 'rightpanel',
    'tpanel': 'toppanel',
    'lpanel': 'leftpanel'
    }

#------------------------------------------------------------------------------#
# Miscellaneous stuff
#------------------------------------------------------------------------------#
# Wrapper functions, so user doesn't have to import pyplot
def close():
    """Alias for ``matplotlib.pyplot.close('all')``. Closes all figures stored
    in memory."""
    plt.close('all') # easy peasy

def show():
    """Alias for ``matplotlib.pyplot.show()``. Note this command should be
    unnecessary if you are doing inline iPython notebook plotting and ran the
    `~proplot.notebook.nbsetup` command."""
    plt.show()

# Helper classes
class _dict(dict):
    """Simple class for accessing elements with dot notation.
    See `this post <https://stackoverflow.com/a/23689767/4970632>`__."""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

class axes_list(list):
    """Magical class that iterates through items and calls respective
    method (or retrieves respective attribute) on each one. For example,
    ``axs.format(color='r')`` colors all axes spines and tick marks red."""
    def __repr__(self):
        """Make clear that this is no ordinary list."""
        return 'axes_list(' + super().__repr__() + ')'

    def __getitem__(self, key):
        """Return an axes_list version of the slice, or just the axes."""
        axs = list.__getitem__(self, key)
        if isinstance(key,slice): # i.e. returns a list
            axs = axes_list(axs)
        return axs

    def __getattr__(self, attr):
        """Stealthily return dummy function that actually loops through each
        axes method and calls them in succession, or returns a list of
        attributes if the attribute is not callable."""
        attrs = [getattr(ax, attr, None) for ax in self]
        if None in attrs:
            raise AttributeError(f'Attribute "{attr}" not found.')
        elif all(callable(_) for _ in attrs):
            @functools.wraps(attrs[0])
            def iterator(*args, **kwargs):
                ret = []
                for attr in attrs:
                    res = attr(*args, **kwargs)
                    if res is not None:
                        ret += [res]
                return None if not ret else ret[0] if len(ret)==1 else ret
            return iterator
        elif all(not callable(_) for _ in attrs):
            return axes_list(attrs)
            # if re.match('^(left|right|top|bottom|l|r|t|b)panel$', attr):
            #     return axes_list(attrs) # just return the attribute list
            # else:
            #     return attrs[0] if len(attrs)==1 else attrs # just return the attribute list
        else:
            raise AttributeError(f'Found mixed types for attribute "{attr}".')

#------------------------------------------------------------------------------#
# Figure class
#------------------------------------------------------------------------------#
def _ax_span(ax, renderer, children=True):
    """Get span, accounting for panels, shared axes, and whether axes has
    been replaced by colorbar in same location."""
    bboxs = [ax.get_tightbbox(renderer)]
    ax = ax._colorbar_parent or ax
    if children:
        children = (ax.leftpanel, ax.bottompanel, ax.rightpanel, ax.toppanel,
            ax.twinx_child, ax.twiny_child)
        for sub in children:
            if not sub:
                continue
            sub = sub._colorbar_child or sub
            if sub.get_visible():
                bboxs.append(sub.get_tightbbox(renderer))
    bboxs = [box for box in bboxs if box is not None]
    xs = np.array([bbox.intervalx for bbox in bboxs])
    ys = np.array([bbox.intervaly for bbox in bboxs])
    xspan = [xs[:,0].min(), xs[:,1].max()]
    yspan = [ys[:,0].min(), ys[:,1].max()]
    return xspan, yspan

def _ax_props(axs, renderer):
    """If this is a panel axes, check if user generated a colorbar axes,
    which stores all the artists/is what we really want to get a
    tight bounding box for."""
    if not axs: # e.g. an "empty panel" was passed
        return (np.empty((0,2)),)*4
    axs = [(ax._colorbar_child or ax) for ax in axs]
    axs = [ax for ax in axs if ax.get_visible()]
    if not axs:
        return (np.empty((0,2)),)*4
    spans = [_ax_span(ax, renderer) for ax in axs]
    xspans = np.array([span[0] for span in spans])
    yspans = np.array([span[1] for span in spans])
    xrange = np.array([(ax._colorbar_parent or ax)._xrange for ax in axs])
    yrange = np.array([(ax._colorbar_parent or ax)._yrange for ax in axs])
    return xspans, yspans, xrange, yrange

# Class
class Figure(mfigure.Figure):
    def __init__(self, figsize, gridspec=None, subplots_kw=None, tight=None,
            outerpad=None, mainpad=None, innerpad=None,
            rcreset=True, silent=True, # print various draw statements?
            **kwargs):
        """
        Matplotlib figure with some pizzazz.

        Parameters
        ----------
        figsize : length-2 list of float or str
            Figure size (width, height). If float, units are inches. If string,
            units are interpreted by `~proplot.utils.units`.
        gridspec : None or `~proplot.gridspec.FlexibleGridSpec`
            Required if `tight` is ``True``.
            The gridspec instance used to fill entire figure.
        subplots_kw : None or dict-like
            Required if `tight` is ``True``.
            Container of the keyword arguments used in the initial call
            to `subplots`.
        tight : None or bool, optional
            If ``True``, when the figure is drawn, its size is conformed to
            a tight bounding box optionally by expanding the width or
            height dimension, without messing up aspect ratios. Inner subplot
            spaces are also perfectly adjusted to prevent large gaps and
            overlapping subplots, tick labels, etc.
            See `~Figure.smart_tight_layout` for details.
            Defaults to ``True``.
        innertight : bool, optional
            If ``True``, the spacing between main axes and inner panels
            is also automatically adjusted. Defaults to ``True``.
        outerpad, mainpad, innerpad : None, float, or str, optional
            Margin size around the tight bounding box around the edge of the
            figure, between main axes in the figure, and between panels
            and their parents, respecitvely. Ignored if `tight` is ``False``.
            If float, units are inches. If string, units are interpreted by
            `~proplot.utils.units`.
        rcreset : bool, optional
            Whether to reset all `~proplot.rcmod.rc` settings to their
            default values once the figure is drawn.
        silent : bool, optional
            Whether to silence messages related to drawing and printing
            the figure.

        Other parameters
        ----------------
        **kwargs : dict-like
            Passed to `matplotlib.figure.Figure` initializer.
        """
        # Initialize figure with some custom attributes.
        # Whether to reset rcParams wheenver a figure is drawn (e.g. after
        # ipython notebook finishes executing)
        self.main_axes = []  # list of 'main' axes (i.e. not insets or panels)
        self._silent  = silent # whether to print message when we resize gridspec
        self._rcreset = rcreset
        self._extra_pad      = 0 # sometimes matplotlib fails, cuts off super title! will add to this
        self._smart_outerpad = units(_default(outerpad, rc['gridspec.outerpad']))
        self._smart_mainpad  = units(_default(innerpad, rc['gridspec.mainpad']))
        self._smart_innerpad = units(_default(innerpad, rc['gridspec.innerpad']))
        self._smart_tight       = _default(tight, rc['tight']) # note name _tight already taken!
        self._smart_tight_inner = _default(tight, rc['innertight']) # note name _tight already taken!
        self._smart_tight_init  = True # is figure in its initial state?
        self._span_labels = [] # add axis instances to this, and label position will be updated
        # Gridspec information
        self._gridspec = gridspec # gridspec encompassing drawing area
        self._subplots_kw = _dict(subplots_kw) # extra special settings
        # Figure dimensions
        figsize = [units(size) for size in figsize]
        self.width  = figsize[0] # dimensions
        self.height = figsize[1]
        # Panels, initiate as empty
        self.leftpanel   = axes.EmptyPanel()
        self.bottompanel = axes.EmptyPanel()
        self.rightpanel  = axes.EmptyPanel()
        self.toppanel    = axes.EmptyPanel()
        # Proceed
        super().__init__(figsize=figsize, **kwargs) # python 3 only
        # Initialize suptitle, adds _suptitle attribute
        self.suptitle('')
        self._suptitle_transform = None

    def __getattribute__(self, attr, *args):
        """Add some aliases."""
        attr = _aliases.get(attr, attr)
        return super().__getattribute__(attr, *args)

    def _lock_twins(self):
        """Lock shared axis limits to these axis limits. Used for secondary
        axis with alternate data scale."""
        # Helper func
        # Note negative heights should not break anything!
        def check(lim, scale):
            if re.match('^log', scale) and any(np.array(lim)<=0):
                raise ValueError('Axis limits go negative, and "alternate units" axis uses log scale.')
            elif re.match('^inverse', scale) and any(np.array(lim)<=0):
                raise ValueError('Axis limits cross zero, and "alternate units" axis uses inverse scale.')
        for ax in self.main_axes:
            # Match units for x-axis
            if ax.dualx_scale:
                # Get stuff
                # transform = mscale.scale_factory(twin.get_xscale(), twin.xaxis).get_transform()
                twin = ax.twiny_child
                offset, scale = ax.dualx_scale
                transform = twin.xaxis._scale.get_transform() # private API
                xlim_orig = ax.get_xlim()
                # Check, and set
                check(xlim_orig, twin.get_xscale())
                xlim = transform.inverted().transform(np.array(xlim_orig))
                if np.sign(np.diff(xlim_orig)) != np.sign(np.diff(xlim)): # the transform flipped it, so when we try to set limits, will get flipped again!
                    xlim = xlim[::-1]
                twin.set_xlim(offset + scale*xlim)
            # Match units for y-axis
            if ax.dualy_scale:
                # Get stuff
                # transform = mscale.scale_factory(twin.get_yscale(), ax.yaxis).get_transform()
                twin = ax.twinx_child
                offset, scale = ax.dualy_scale
                transform = twin.yaxis._scale.get_transform() # private API
                ylim_orig = ax.get_ylim()
                check(ylim_orig, twin.get_yscale())
                ylim = transform.inverted().transform(np.array(ylim_orig))
                if np.sign(np.diff(ylim_orig)) != np.sign(np.diff(ylim)): # dunno why needed
                    ylim = ylim[::-1]
                twin.set_ylim(offset + scale*ylim) # extra bit comes after the forward transformation

    def _rowlabels(self, labels, **kwargs):
        """Assign row labels."""
        axs = [ax for ax in self.main_axes if ax._xrange[0]==0]
        if isinstance(labels, str): # common during testing
            labels = [labels]*len(axs)
        if len(labels)!=len(axs):
            raise ValueError(f'Got {len(labels)} labels, but there are {len(axs)} rows.')
        axs = [ax for _,ax in sorted(zip([ax._yrange[0] for ax in axs], axs))]
        for ax,label in zip(axs,labels):
            if label and not ax.rowlabel.get_text():
                # Create a CompositeTransform that converts coordinates to
                # universal dots, then back to axes
                label_to_ax = ax.yaxis.label.get_transform() + ax.transAxes.inverted()
                x, _ = label_to_ax.transform(ax.yaxis.label.get_position())
                ax.rowlabel.update({'x':x, 'text':f'{label.strip()}   ', 'visible':True, **kwargs})

    def _collabels(self, labels, **kwargs):
        """Assign column labels."""
        axs = [ax for ax in self.main_axes if ax._yrange[0]==0]
        if isinstance(labels, str):
            labels = [labels]*len(axs)
        if len(labels)!=len(axs):
            raise ValueError(f'Got {len(labels)} labels, but there are {len(axs)} columns.')
        axs = [ax for _,ax in sorted(zip([ax._xrange[0] for ax in axs],axs))]
        for ax,label in zip(axs,labels):
            if ax.toppanel.on():
                ax = ax.toppanel
            if label and not ax.collabel.get_text():
                ax.collabel.update({'text':label, **kwargs})

    def _suptitle_setup(self, renderer=None, **kwargs):
        """Intelligently determine super title position."""
        # Seems that `~matplotlib.figure.Figure.draw` is called *more than once*,
        # and the title position are appropriately offset only during the
        # *later* calls! So need to run this every time.
        # Row label; need to update as axis tick labels are generated and axis
        # label is offset!
        for ax in self.main_axes:
            if ax.toppanel.on():
                ax = ax.toppanel
            label_to_ax = ax.yaxis.label.get_transform() + ax.transAxes.inverted()
            x, _ = label_to_ax.transform(ax.yaxis.label.get_position())
            ax.rowlabel.update({'x':x, 'ha':'center', 'transform':ax.transAxes})

        # Super title
        # Determine x by the underlying gridspec structure, where main axes lie.
        left = self._subplots_kw.left
        right = self._subplots_kw.right
        if self.leftpanel:
            left += (self._subplots_kw.lwidth + self._subplots_kw.lspace)
        if self.rightpanel:
            right += (self._subplots_kw.rwidth + self._subplots_kw.rspace)
        xpos = left/self.width + 0.5*(self.width - left - right)/self.width

        # Figure out which title on the top-row axes will be offset the most
        # See: https://matplotlib.org/_modules/matplotlib/axis.html#Axis.set_tick_params
        # TODO: Modify self.axes? Maybe add a axes_main and axes_panel
        # attribute or something? Because if have insets and other stuff
        # won't that fuck shit up?
        # WARNING: Have to use private API to figure out whether axis has
        # tick labels or not! And buried in docs (https://matplotlib.org/api/_as_gen/matplotlib.axis.Axis.set_ticklabels.html)
        # label1 refers to left or bottom, label2 to right or top!
        # WARNING: Found with xtickloc='both', xticklabelloc='top' or 'both',
        # got x axis ticks position 'unknown'!
        xlabel = 0 # room for x-label
        title_lev1, title_lev2, title_lev3 = None, None, None
        raxs = [ax for ax in self.main_axes if ax._yrange[0]==0]
        raxs = [ax.toppanel for ax in raxs if ax.toppanel.on()] or raxs # filter to just these if top panel is enabled
        for axm in raxs:
            axs = [ax for ax in (axm, axm.twinx_child, axm.twiny_child) if ax]
            if axm.leftpanel.on():
                axs.append(axm.leftpanel)
            if axm.rightpanel.on():
                axs.append(axm.rightpanel)
            for ax in axs:
                title_lev1 = ax.title # always will be non-None
                if (ax.title.get_text().strip() and not ax._title_inside) or ax.collabel.get_text().strip():
                    title_lev2 = ax.title
                pos = ax.xaxis.get_ticks_position()
                if pos in ('top', 'unknown', 'default'):
                    label_top = pos!='default' and ax.xaxis.label.get_text()
                    ticklabels_top = ax.xaxis._major_tick_kw['label2On']
                    if ticklabels_top:
                        title_lev3 = ax.title
                        if label_top:
                            xlabel = 1.2*(ax.xaxis.label.get_size()/72)/self.height
                    elif label_top:
                        title_lev3 = ax.title

        # Hacky bugfixes
        # NOTE: Default linespacing is 1.2; it has no get, only a setter.
        # See: https://matplotlib.org/api/text_api.html#matplotlib.text.Text.set_linespacing
        if title_lev3:
            # If title and tick labels on top
            # WARNING: Matplotlib tight subplot will screw up, not detect
            # super title. Adding newlines does not help! We fix this manually.
            line = 2.4 # 2.4
            title = title_lev3
            self._extra_pad = 1.2*(self._suptitle.get_size()/72)/self.height
        elif title_lev2:
            # Just offset suptitle, and matplotlib will recognize it
            line = 1.2
            title = title_lev2
        else:
            # No title present. So, fill with spaces. Does nothing in most
            # cases, but if tick labels are on top, without this step
            # matplotlib tight subplots will not see the suptitle.
            line = 0
            title = title_lev1
            if not title.axes._title_inside:
                title.set_text(' ')
        # Get the transformed position
        if self._suptitle_transform:
            transform = self._suptitle_transform
        else:
            transform = title.axes._title_pos_transform + self.transFigure.inverted()
        ypos = transform.transform(title.axes._title_pos_init)[1]
        line = line*(title.get_size()/72)/self.height + xlabel
        ypos = ypos + line

        # Update settings
        self._suptitle.update({
            'position': (xpos, ypos), 'transform': self.transFigure,
            'ha':'center', 'va':'bottom',
            **kwargs})

    def _auto_smart_tight_layout(self, renderer=None):
        """Conditionally call `~Figure.smart_tight_layout`."""
        # Only proceed if this wasn't already done, or user did not want
        # the figure to have tight boundaries.
        if not self._smart_tight_init or not self._smart_tight:
            return
        # Bail if we find cartopy axes (TODO right now set_bounds may mess up tight subplot still)
        # 1) If you used set_bounds to zoom into part of a cartopy projection,
        # this can erroneously identify invisible edges of map as being part of boundary
        # 2) If you have gridliner text labels, matplotlib won't detect them.
        if any(ax._gridliner_on for ax in self.main_axes):
            return
        # Proceed
        if not self._silent:
            print('Adjusting gridspec.')
        self.smart_tight_layout(renderer)

    def _inner_tight_layout(self, panels, spans, renderer, side='l'):
        """From list of panels and the axes coordinates spanned by the
        main axes, figure out the necessary new 'spacing' to apply to the inner
        gridspec object."""
        # Initial stuff
        pad = self._smart_innerpad
        if all(not panel for panel in panels): # exists, but may be invisible
            return
        elif not all(panel for panel in panels):
            raise ValueError('Either all or no axes in this row should have panels.')
        # Iterate through panels, get maximum necessary spacing; assign
        # equal spacing to all so they are still aligned
        space = []
        gspecs = [panel.get_subplotspec().get_gridspec() for panel in panels]
        if side in 'lr':
            ratios = [gs.get_width_ratios() for gs in gspecs]
        else:
            ratios = [gs.get_height_ratios() for gs in gspecs]
        for panel, span in zip(panels, spans):
            if not panel.on() and not panel._colorbar_child:
                continue
            # ic(panel._side, panel._parent.number)
            bbox = (panel._colorbar_child or panel).get_tightbbox(renderer)
            if side in 'lr':
                pspan = bbox.intervalx
            else:
                pspan = bbox.intervaly
            if side in 'tr':
                space.append((pspan[0] - span[1])/self.dpi)
            else:
                space.append((span[0] - pspan[1])/self.dpi)
        # Finally update the ratios
        # NOTE: Should just be able to modify mutable width and height ratios
        # list, do not need setter! The getter does not return a copy or anything.
        if not space:
            warnings.warn('All panels in this row or column are invisible. That is really weird.')
            return
        # pad = 0
        for ratio in ratios:
            idx = (-2 if side in 'br' else 1)
            # ratio[idx] = 0
            ratio[idx] = max([0, ratio[idx] - min(space) + pad])

    def smart_tight_layout(self, renderer=None):
        """
        This is called automatically when `~Figure.draw` or
        `~Figure.savefig` are called,
        unless the user set `tight` to ``False`` in their original
        call to `subplots`.

        Conforms figure edges to tight bounding box around content, without
        screwing up subplot aspect ratios, empty spaces, panel sizes, and such.
        Also fixes inner spaces, so that e.g. axis tick labels and axis labels
        do not overlap with other axes (which was even harder to do and is
        insanely awesome).

        Parameters
        ----------
        renderer : None or `~matplotlib.backend_bases.RendererBase`, optional
            The backend renderer.
        """
        #----------------------------------------------------------------------#
        # Put tight box *around* figure
        #----------------------------------------------------------------------#
        # Get bounding box that encompasses *all artists*, compare to bounding
        # box used for saving *figure*
        pad = self._smart_outerpad
        if self._subplots_kw is None or self._gridspec is None:
            warnings.warn('Could not find "_subplots_kw" or "_gridspec" attributes, cannot get tight layout.')
            self._smart_tight_init = False
            return
        obbox = self.bbox_inches # original bbox
        if not renderer: # cannot use the below on figure save! figure becomes a special FigurePDF class or something
            renderer = self.canvas.get_renderer()
        bbox = self.get_tightbbox(renderer)
        ox, oy, x, y = obbox.intervalx, obbox.intervaly, bbox.intervalx, bbox.intervaly
        if np.any(np.isnan(x) | np.isnan(y)):
            warnings.warn('Bounding box has NaNs, cannot get tight layout.')
            self._smart_tight_init = False
            return
        # Apply new kwargs
        subplots_kw = self._subplots_kw
        left, bottom, right, top = x[0], y[0], ox[1]-x[1], oy[1]-y[1] # want *deltas*
        left   = subplots_kw.left - left + pad
        right  = subplots_kw.right - right + pad
        bottom = subplots_kw.bottom - bottom + pad
        top    = subplots_kw.top - top + pad + self._extra_pad
        subplots_kw.update({'left':left, 'right':right, 'bottom':bottom, 'top':top})

        #----------------------------------------------------------------------#
        # Prevent overlapping axis tick labels and whatnot *within* figure
        #----------------------------------------------------------------------#
        # First for the main axes, then add in the panels
        xspans, yspans, xrange, yrange = _ax_props(self.main_axes, renderer)
        for panel in (self.leftpanel, self.bottompanel, self.rightpanel, self.toppanel):
            xs, ys, xr, yr = _ax_props(panel, renderer)
            xspans, yspans = np.vstack((xspans, xs)), np.vstack((yspans, ys))
            xrange, yrange = np.vstack((xrange, xr)), np.vstack((yrange, yr))
        xrange[:,1] += 1 # now, ids are not endpoint inclusive
        yrange[:,1] += 1
        # Construct "wspace" and "hspace" including panel spaces
        wspace_orig, hspace_orig = subplots_kw.wspace, subplots_kw.hspace # originals
        if self.leftpanel:
            wspace_orig = [subplots_kw.lspace, *wspace_orig]
        if self.rightpanel:
            wspace_orig = [*wspace_orig, subplots_kw.rspace]
        if self.bottompanel:
            hspace_orig = [*hspace_orig, subplots_kw.bspace]
        # Find groups of axes with touching left/right sides
        # We generate a list (xgroups), each element corresponding to a
        # column of *space*, containing lists of dictionaries with 'l' and
        # 'r' keys, describing groups of "touching" axes in that column
        xgroups = []
        ncols = self.main_axes[0]._ncols
        nrows = self.main_axes[0]._nrows
        for cspace in range(1, ncols): # spaces between columns
            groups = []
            for row in range(nrows):
                filt = (yrange[:,0] <= row) & (row < yrange[:,1])
                if sum(filt)<=1: # no interface here
                    continue
                right, = np.where(filt & (xrange[:,0] == cspace))
                left,  = np.where(filt & (xrange[:,1] == cspace))
                if not (left.size==1 and right.size==1):
                    continue # normally both zero, but one can be non-zero e.g. if have left and bottom panel, empty space in corner
                left, right = left[0], right[0]
                added = False
                for group in groups:
                    if left in group['l'] or right in group['r']:
                        group['l'].update((left,))
                        group['r'].update((right,))
                        added = True
                        break
                if not added:
                    groups.append({'l':{left}, 'r':{right}}) # form new group
            xgroups.append(groups)
        # Find groups of axes with touching bottom/top sides
        ygroups = []
        for rspace in range(1, nrows): # spaces between rows
            groups = []
            for col in range(ncols):
                filt = (xrange[:,0] <= col) & (col < xrange[:,1])
                top,    = np.where(filt & (yrange[:,0] == rspace))
                bottom, = np.where(filt & (yrange[:,1] == rspace))
                if not (bottom.size==1 and top.size==1):
                    continue
                bottom, top = bottom[0], top[0]
                added = False
                for group in groups:
                    if bottom in group['b'] or top in group['t']:
                        group['b'].update((bottom,))
                        group['t'].update((top,))
                        added = True
                        break
                if not added:
                    groups.append({'b':{bottom}, 't':{top}}) # form new group
            ygroups.append(groups)
        # Correct wspace and hspace
        pad = self._smart_mainpad
        wspace, hspace = [], []
        for space_orig, groups in zip(wspace_orig, xgroups):
            if not groups:
                wspace.append(space_orig)
                continue
            seps = [] # will use *maximum* necessary separation
            for group in groups: # groups with touching edges
                left = max(xspans[idx,1] for idx in group['l']) # axes on left side of column
                right = min(xspans[idx,0] for idx in group['r']) # axes on right side of column
                seps.append((right - left)/self.dpi)
            wspace.append(max((0, space_orig - min(seps) + pad)))
        for space_orig, groups in zip(hspace_orig, ygroups):
            if not groups:
                hspace.append(space_orig)
                continue
            seps = [] # will use *maximum* necessary separation
            for group in groups: # groups with touching edges
                # bottom = min(yspans[idx,0] for idx in group['b'])
                # top = max(yspans[idx,1] for idx in group['t'])
                bottom = max(yspans[idx,0] for idx in group['b'])
                top = min(yspans[idx,1] for idx in group['t'])
                seps.append((bottom - top)/self.dpi)
            hspace.append(max((0, space_orig - min(seps) + pad)))
        # If had panels, need to pull out args
        lspace, rspace, bspace = 0, 0, 0 # does not matter if no panels
        if self.leftpanel:
            lspace, *wspace = wspace
        if self.rightpanel:
            *wspace, rspace = wspace
        if self.bottompanel:
            *hspace, bspace = hspace
        subplots_kw.update({'wspace':wspace, 'hspace':hspace,
            'lspace':lspace, 'rspace':rspace, 'bspace':bspace})

        #----------------------------------------------------------------------#
        # The same, but for spaces inside *inner panels*
        #----------------------------------------------------------------------#
        # Get bounding boxes and intervals, otherwise they are queried a bunch
        # of times which may slow things down unnecessarily (benchmark?)
        if self._smart_tight_inner:
            axs = self.main_axes
            bboxs = [ax.get_tightbbox(renderer) for ax in axs]
            xspans = [bbox.intervalx for bbox in bboxs]
            yspans = [bbox.intervaly for bbox in bboxs]
            # Bottom, top
            for row in range(nrows):
                pairs = [(ax.bottompanel, yspan) for ax,yspan in zip(axs,yspans) if ax._yrange[1]==row]
                self._inner_tight_layout(*zip(*pairs), renderer, side='b')
                pairs = [(ax.toppanel, yspan) for ax,yspan in zip(axs,yspans) if ax._yrange[0]==row]
                self._inner_tight_layout(*zip(*pairs), renderer, side='t')
            # Left, right
            for col in range(ncols):
                pairs = [(ax.leftpanel, xspan) for ax,xspan in zip(axs,xspans) if ax._xrange[0]==col]
                self._inner_tight_layout(*zip(*pairs), renderer, side='l')
                pairs = [(ax.rightpanel, xspan) for ax,xspan in zip(axs,xspans) if ax._xrange[1]==col]
                self._inner_tight_layout(*zip(*pairs), renderer, side='r')
            # Get the wextra and hextra kwargs
            ax = self.main_axes[0]
            gs = ax.get_subplotspec().get_gridspec()
            wextra = sum(w for i,w in enumerate(gs.get_width_ratios())
                if i!=2*int(bool(ax.leftpanel))) # main subplot index is position 2 of left panel is present, 0 if not
            hextra = sum(w for i,w in enumerate(gs.get_height_ratios())
                if i!=2*int(bool(ax.toppanel))) # as for wextra
            subplots_kw.update({'wextra':wextra, 'hextra':hextra})

        #----------------------------------------------------------------------#
        # Finish
        #----------------------------------------------------------------------#
        # Parse arguments and update stuff
        # WARNING: Need to update gridspec twice. First to get the width
        # and height ratios including spaces, then after because gridspec
        # changes propagate only *down*, not up; will not be applied otherwise.
        figsize, _, gridspec_kw = _parse_args(**subplots_kw)
        self._smart_tight_init = False
        self._gridspec.update(**gridspec_kw)
        self.set_size_inches(figsize)
        self.width, self.height = figsize

        # Update width and height ratios of axes inner panel subplotspec,
        # to reflect the new axes heights and widths (ratios are stored in physical units, inches)
        # width_new = np.diff(ax._position.intervalx)*self.width
        # height_new = np.diff(ax._position.intervaly)*self.height
        for ax in self.main_axes:
            # Get *new* axes dimensions
            if not any(bool(panel) for panel in (ax.bottompanel, ax.toppanel, ax.leftpanel, ax.rightpanel)):
                continue
            xrange = 2*np.array(ax._xrange) # endpoint inclusive
            yrange = 2*np.array(ax._yrange)
            wratios = self._gridspec.get_width_ratios()
            hratios = self._gridspec.get_height_ratios()
            axwidth = sum(wratios[xrange[0]:xrange[1]+1]) - subplots_kw.wextra
            axheight = sum(hratios[yrange[0]:yrange[1]+1]) - subplots_kw.hextra
            # Update inner panel axes
            gs = ax.get_subplotspec().get_gridspec()
            wratios = gs.get_width_ratios() # gets my *custom* ratios
            hratios = gs.get_height_ratios()
            hratios[2 if ax.toppanel else 0] = axheight
            wratios[2 if ax.leftpanel else 0] = axwidth
            gs.set_width_ratios(wratios)
            gs.set_height_ratios(hratios)
        # Final update and we're good to go
        self._gridspec.update(**gridspec_kw)

    def draw(self, renderer, *args, **kwargs):
        """Fix the "super title" position and automatically adjust the
        main gridspec, then call the parent `~matplotlib.figure.Figure.draw`
        method."""
        # Special: Figure out if other titles are present, and if not
        # bring suptitle close to center
        # Also fix spanning labels (they need figure-relative height
        # coordinates, so must be updated when figure shape changes).
        self._lock_twins()
        self._suptitle_setup(renderer) # just applies the spacing
        self._auto_smart_tight_layout(renderer)
        for axis in self._span_labels:
            axis.axes._share_span_label(axis, final=True)
        out = super().draw(renderer, *args, **kwargs)

        # If rc settings have been changed, reset them after drawing is done
        # Usually means we have finished executing a notebook cell
        if not rc._init and self._rcreset:
            if not self._silent:
                print('Resetting rcparams.')
            rc.reset()
        return out

    def save(self, *args, **kwargs):
        """Alias for `~Figure.savefig`."""
        return self.savefig(*args, **kwargs)

    def savefig(self, filename, **kwargs):
        """
        Fix the "super title" position and automatically adjust the
        main gridspec, then call the parent `~matplotlib.figure.Figure.savefig`
        method.

        Parameters
        ----------
        filename : str
            The file name and path.
        **kwargs
            Passed to the matplotlib `~matplotlib.figure.Figure.savefig` method.
        """
        # Notes
        # * Gridspec object must be updated before figure is printed to
        #     screen in interactive environment; will fail to update after that.
        #     Seems to be glitch, should open thread on GitHub.
        # * To color axes patches, you may have to explicitly pass the
        #     transparent=False kwarg.
        #     Some kwarg translations, to pass to savefig
        if 'alpha' in kwargs:
            kwargs['transparent'] = not bool(kwargs.pop('alpha')) # 1 is non-transparent
        if 'color' in kwargs:
            kwargs['facecolor'] = kwargs.pop('color') # the color
            kwargs['transparent'] = False
        # Finally, save
        self._lock_twins()
        self._suptitle_setup() # just applies the spacing
        self._auto_smart_tight_layout()
        for axis in self._span_labels:
            axis.axes._share_span_label(axis, final=True)
        if not self._silent:
            print(f'Saving to "{filename}".')
        return super().savefig(os.path.expanduser(filename), **kwargs) # specify DPI for embedded raster objects

    def panel_factory(self, subspec, which=None, whichpanels=None,
            lwidth=None, rwidth=None, twidth=None, bwidth=None,
            lvisible=True, rvisible=True, tvisible=True, bvisible=True, # make panel invisible if they are just there to accomadate space, and user did not request them
            hspace=None, wspace=None,
            lshare=True, rshare=True, bshare=True, tshare=True, # whether to share main subplot axis with panel axis
            **kwargs):
        """
        Automatically called by `subplots`.
        Creates "inner" panels, i.e. panels along subplot edges.

        Parameters
        ----------
        subspec : `~matplotlib.gridspec.SubplotSpec`
            The `~matplotlib.gridspec.SubplotSpec` instance onto which
            the main subplot and its panels are drawn.
        which
            Alias for `whichpanels`.
        whichpanels : None or str, optional
            Whether to draw panels on the left, right, bottom, or top
            sides. Should be a string containing any of the characters
            ``'l'``, ``'r'``, ``'b'``, or ``'t'`` in any order.
            Default is ``'r'``.
        lwidth, rwidth, bwidth, twidth : None, float, or str, optional
            Width of left, right, bottom, and top panels, respectively.
            If float, units are inches. If string, units are interpreted
            by `~proplot.utils.units`.
        wspace, hspace : None, float, or str, optional
            Empty space between the main subplot and the panel.
            If float, units are inches. If string,
            units are interpreted by `~proplot.utils.units`.
        lshare, rshare, bshare, tshare : bool, optional
            Whether to enable axis sharing between *y* axis of main subplot
            and left, right panels, and between *x* axis of main subplot and
            bottom, top panels. Defaults to ``True`` for all.

        Notes
        -----
        Axis sharing setup is handled after-the-fact in the `subplots`
        function using `~proplot.axes.BaseAxes._sharex_setup` and
        `~proplot.axes.BaseAxes._sharey_setup`.

        Todo
        ----
        Make settings specific to left, right, top, bottom panels!
        """
        # Helper function for creating paneled axes.
        which = _default(which, whichpanels, 'r')
        lwidth = units(_default(lwidth, rc['gridspec.panelwidth'])) # default is panels for plotting stuff, not colorbars
        rwidth = units(_default(rwidth, rc['gridspec.panelwidth']))
        twidth = units(_default(twidth, rc['gridspec.panelwidth']))
        bwidth = units(_default(bwidth, rc['gridspec.panelwidth']))
        hspace = np.atleast_1d(units(_default(hspace, rc['gridspec.panelspace']))) # teeny tiny space
        wspace = np.atleast_1d(units(_default(wspace, rc['gridspec.panelspace'])))
        if re.sub('[lrbt]', '', which): # i.e. other characters are present
            raise ValueError(f'Whichpanels argument can contain characters l (left), r (right), b (bottom), or t (top), instead got "{whichpanels}".')

        # Determine rows/columns and indices
        nrows = 1 + len(re.sub('[^bt]', '', which))
        ncols = 1 + len(re.sub('[^lr]', '', which))
        rows = [l for l in ['l', None, 'r'] if not l or l in which]
        cols = [l for l in ['t', None, 'b'] if not l or l in which]
        # Detect empty positions and main axes position
        center  = (int('t' in which), int('l' in which))
        corners = {'tl':(0,0),           'tr':(0,center[1]+1),
                   'bl':(center[0]+1,0), 'br':(center[0]+1,center[1]+1)}
        empty = [position for corner,position in corners.items()
                 if len({*corner} & {*which})==2] # check if both chars present

        # Fix wspace/hspace in inches, using the Bbox from get_postition
        # on the subspec object to determine physical width of axes to be created
        bbox = subspec.get_position(self) # valid since axes not drawn yet
        # Fix heights
        if hspace.size==1:
            hspace = np.repeat(hspace, (nrows-1,))
        boxheight = np.diff(bbox.intervaly)[0]*self.height
        height = boxheight - hspace.sum()
        # hspace = hspace/(height/nrows)
        # Fix width
        if wspace.size==1:
            wspace = np.repeat(wspace, (ncols-1,))
        boxwidth = np.diff(bbox.intervalx)[0]*self.width
        width = boxwidth - wspace.sum()
        # wspace = wspace/(width/ncols)

        # Figure out hratios/wratios
        # Will enforce (main_width + panel_width)/total_width = 1
        wratios = [width]
        if 'l' in which:
            wratios = [lwidth, wratios[0] - lwidth, *wratios[1:]]
        if 'r' in which:
            wratios = [*wratios[:-1], wratios[-1] - rwidth, rwidth]
        hratios = [height]
        if 't' in which:
            hratios = [twidth, hratios[0] - twidth, *hratios[1:]]
        if 'b' in which:
            hratios = [*hratios[:-1], hratios[-1] - bwidth, bwidth]
        if any(w < 0 for w in wratios):
            raise ValueError(f'Left and/or right panel widths too large for available space {width}in.')
        if any(h < 0 for h in hratios):
            raise ValueError(f'Top and/or bottom panel widths too large for available space {height}in.')

        # Create subplotspec and draw the axes
        # Will create axes in order of rows/columns so that the "base" axes
        # are always built before the axes to be "shared" with them
        panels = {}
        gs = gridspec.FlexibleGridSpecFromSubplotSpec(subplot_spec=subspec,
                nrows=nrows, ncols=ncols,
                wspace=wspace, hspace=hspace,
                width_ratios=wratios, height_ratios=hratios)
        ax = self.add_subplot(gs[center[0], center[1]], **kwargs)
        kwargs = {key:value for key,value in kwargs.items() if key not in ('number', 'projection')}
        for r,col in enumerate(cols): # iterate top-bottom
            for c,row in enumerate(rows): # iterate left-right
                if (r,c) in empty or (r,c)==center:
                    continue
                side = {'b':'bottom', 't':'top', 'l':'left', 'r':'right'}[col or row]
                pax = self.add_subplot(gs[r,c], side=side, parent=ax, projection='panel', **kwargs)
                setattr(ax, f'{side}panel', pax)
        # Optionally make axes invisible
        # This is done so that axes in subplots are aligned even when their
        # inner panels are not the same.
        if not lvisible and ax.leftpanel:
            ax.leftpanel.set_visible(False)
        if not rvisible and ax.rightpanel:
            ax.rightpanel.set_visible(False)
        if not bvisible and ax.bottompanel:
            ax.bottompanel.set_visible(False)
        if not tvisible and ax.toppanel:
            ax.toppanel.set_visible(False)
        # Set up axis sharing
        # * Sharex and sharey methods should be called on the 'child' axes,
        #   with labelling preferred on the axes in the first argument.
        # * This block must come *after* the above; sharex_setup and
        #   sharey_setup will detect invisible panels and not share with them.
        # First the x-axis
        if bshare and ax.bottompanel.on():
            ax.bottompanel._share = True
            ax._sharex_setup(ax.bottompanel, 3)
        if tshare and ax.toppanel.on():
            bottom = ax.bottompanel if bshare and ax.bottompanel.on() else ax
            ax.toppanel._share = True
            ax.toppanel._sharex_setup(bottom, 3)
        # Now the y-axis
        if lshare and ax.leftpanel.on():
            ax.leftpanel._share = True
            ax._sharey_setup(ax.leftpanel, 3)
        if rshare and ax.rightpanel.on():
            left = ax.leftpanel if lshare and ax.leftpanel.on() else ax
            ax.rightpanel._share = True
            ax.rightpanel._sharey_setup(left, 3)
        return ax

#-------------------------------------------------------------------------------
# Primary plotting function; must be used to create figure/axes if user wants
# to use the other features
#-------------------------------------------------------------------------------
# Helper functions
def _panel2panels(panel, panels, nmax):
    """Convert 'panel' argument to vector indicating panels positions."""
    if panel: # one spanning panel
        panels = [1]*nmax
    elif panels not in (None,False): # can't test truthiness, want user to be allowed to pass numpy vector!
        try:
            panels = list(panels)
        except TypeError:
            panels = [*range(nmax)] # pass True to make panel for each column
    return panels

def _panelprops(panel, panels, colorbar, colorbars, legend, legends, width, space):
    """Change defalut panel properties depending on variation of keyword arg
    selected by user. For example, use "panel" when a panel for plotting stuff
    is desired, and "colorbar" when a panel filled with a colorbar is desired."""
    if colorbar or colorbars:
        width = units(_default(width, rc['gridspec.cbarwidth']))
        space = units(_default(space, rc['gridspec.xlabspace']))
        panel, panels = colorbar, colorbars
    elif legend or legends:
        width = units(_default(width, rc['gridspec.legwidth']))
        space = units(_default(space, 0))
        panel, panels = legend, legends
    return panel, panels, width, space

def _parse_panels(nrows, ncols,
    # Final keyword args
    bwidth=None, bspace=None, rwidth=None, rspace=None, lwidth=None, lspace=None, # default to no space between panels
    bottompanels=False, rightpanels=False, leftpanels=False,
    # Modified by any of the below keyword args
    bottompanel=False, rightpanel=None, leftpanel=None,
    bottomcolorbar=False, bottomcolorbars=False, bottomlegend=False, bottomlegends=False, # convenient aliases that change default features
    rightcolorbar=False,  rightcolorbars=False,  rightlegend=False,  rightlegends=False,
    leftcolorbar=False,   leftcolorbars=False,   leftlegend=False,   leftlegends=False,
    bpanel=None, bpanels=None,
    rpanel=None, rpanels=None,
    lpanel=None, lpanels=None,
    bcolorbar=None, bcolorbars=None, blegend=None, blegends=None,
    rcolorbar=None, rcolorbars=None, rlegend=None, rlegends=None,
    lcolorbar=None, lcolorbars=None, llegend=None, llegends=None,
    **kwargs
    ):
    """Helper function, handles optional panel arguments. Separate function
    because of some internal stuff we do in subplots command."""
    # Shorthand aliases, because why not?
    bottompanel  = bpanel or bottompanel
    bottompanels = bpanels or bottompanels
    rightpanel   = rpanel or rightpanel
    rightpanels  = rpanels or rightpanels
    leftpanel    = lpanel or leftpanel
    leftpanels   = lpanels or leftpanels
    bottomlegend  = blegend or bottomlegend
    bottomlegends = blegends or bottomlegends # convenient aliases that change default features
    rightlegend   = rlegend or rightlegend
    rightlegends  = rlegends or rightlegends
    leftlegend    = llegend or leftlegend
    leftlegends   = llegends or leftlegends
    bottomcolorbar  = bcolorbar or bottomcolorbar
    bottomcolorbars = bcolorbars or bottomcolorbars
    rightcolorbar   = rcolorbar or rightcolorbar
    rightcolorbars  = rcolorbars or rightcolorbars
    leftcolorbar    = lcolorbar or leftcolorbar
    leftcolorbars   = lcolorbars or leftcolorbars
    # Default panel properties
    bwidth = units(_default(bwidth, rc['gridspec.cbarwidth']))
    rwidth = units(_default(rwidth, rc['gridspec.cbarwidth']))
    lwidth = units(_default(lwidth, rc['gridspec.cbarwidth']))
    bspace = units(_default(bspace, rc['gridspec.xlabspace']))
    rspace = units(_default(rspace, rc['gridspec.ylabspace']))
    lspace = units(_default(lspace, rc['gridspec.ylabspace']))
    # Handle the convenience feature for specifying the desired width/spacing
    # for panels as that suitable for a colorbar or legend
    rightpanel, rightpanels, rwidth, rspace, = _panelprops(
        rightpanel, rightpanels, rightcolorbar, rightcolorbars,
        rightlegend, rightlegends, rwidth, rspace)
    leftpanel, leftpanels, lwidth, lspace = _panelprops(
        leftpanel, leftpanels, leftcolorbar, leftcolorbars,
        leftlegend, leftlegends, lwidth, lspace)
    bottompanel, bottompanels, bwidth, bspace = _panelprops(
        bottompanel, bottompanels, bottomcolorbar, bottomcolorbars,
        bottomlegend, bottomlegends, bwidth, bspace)
    # Handle the convenience feature for generating one panel per row/column
    # and one single panel for all rows/columns
    leftpanels   = _panel2panels(leftpanel,   leftpanels,   nrows)
    rightpanels  = _panel2panels(rightpanel,  rightpanels,  nrows)
    bottompanels = _panel2panels(bottompanel, bottompanels, ncols)
    # Return updated, consolidted kwargs
    kwargs.update({
        'leftpanels':leftpanels, 'rightpanels':rightpanels, 'bottompanels':bottompanels,
        'bwidth':bwidth, 'bspace':bspace, 'rwidth':rwidth, 'rspace':rspace,
        'lwidth':lwidth, 'lspace':lspace,
        })
    return kwargs # also return *leftover* kwargs

def _parse_args(nrows, ncols, rowmajor=True, aspect=1, figsize=None,
    wextra=0, hextra=0, # extra space associated with inner panels; can be *arrays* matching number of columns, rows
    left=None, bottom=None, right=None, top=None, # spaces around edge of main plotting area, in inches
    width=None,  height=None, axwidth=None, axheight=None, journal=None,
    hspace=None, wspace=None, hratios=None, wratios=None, # spacing between axes, in inches (hspace should be bigger, allowed room for title)
    bottompanels=None, leftpanels=None, rightpanels=None,
    bwidth=None, bspace=None, rwidth=None, rspace=None, lwidth=None, lspace=None, # default to no space between panels
    ):
    """Handle complex keyword args and aliases thereof. Note bwidth, bspace,
    etc. must be supplied or will get error, but this should be taken care
    of by _parse_panels."""
    # Defualt gridspec props
    left   = units(_default(left,   rc['gridspec.ylabspace']))
    bottom = units(_default(bottom, rc['gridspec.xlabspace']))
    right  = units(_default(right,  rc['gridspec.nolabspace']))
    top    = units(_default(top,    rc['gridspec.titlespace']))
    wratios = np.atleast_1d(_default(wratios, 1))
    hratios = np.atleast_1d(_default(hratios, 1))
    hspace = np.atleast_1d(_default(hspace, rc['gridspec.titlespace']))
    wspace = np.atleast_1d(_default(wspace, rc['gridspec.innerspace']))
    if len(wratios)==1:
        wratios = np.repeat(wratios, (ncols,))
    if len(hratios)==1:
        hratios = np.repeat(hratios, (nrows,))
    if len(wspace)==1:
        wspace = np.repeat(wspace, (ncols-1,))
    if len(hspace)==1:
        hspace = np.repeat(hspace, (nrows-1,))
    # Dimensions with arbitrary units, or from journal name
    if journal:
        if width or height or axwidth or axheight or figsize:
            raise ValueError('Argument conflict: Specify a journal size or dimension size, not both.')
        width, height = journals(journal) # if user passed width=<string>, will use that journal size
    if not figsize:
        figsize = (width, height)
    width, height = figsize
    width  = units(width, error=False) # if None, returns None
    height = units(height, error=False)
    axwidth = units(axwidth, error=False)
    axheight = units(axheight, error=False)

    # Determine figure size
    # If width and height are not fixed, will scale them to preserve aspect
    # ratio of the first plot
    auto_both = (width is None and height is None)
    auto_width  = (width is None and height is not None)
    auto_height = (height is None and width is not None)
    auto_neither = (width is not None and height is not None)
    bpanel_space = bwidth + bspace if bottompanels else 0
    rpanel_space = rwidth + rspace if rightpanels else 0
    lpanel_space = lwidth + lspace if leftpanels else 0
    # Fix aspect ratio, in light of row and column ratios
    if np.iterable(aspect):
        aspect = aspect[0]/aspect[1]
    aspect_fixed = aspect/(wratios[0]/np.mean(wratios)) # e.g. if 2 columns, 5:1 width ratio, change the 'average' aspect ratio
    aspect_fixed = aspect*(hratios[0]/np.mean(hratios))
    # Determine average axes widths/heights
    # Default behavior: axes average 2.0 inches wide
    if auto_width or auto_neither:
        axheights = height - top - bottom - sum(hspace) - bpanel_space
    if auto_height or auto_neither:
        axwidths = width - left - right - sum(wspace) - rpanel_space - lpanel_space
    # If both are auto (i.e. no figure dims specified), this is where
    # we set figure dims according to axwidth and axheight input
    if auto_both: # get stuff directly from axes
        if axwidth is None and axheight is None:
            axwidth = units(rc['gridspec.axwidth'])
        if axheight is not None:
            height = axheight*nrows + top + bottom + sum(hspace) + bpanel_space
            axheights = axheight*nrows
            auto_width = True
        if axwidth is not None:
            width = axwidth*ncols + left + right + sum(wspace) + rpanel_space + lpanel_space
            axwidths = axwidth*ncols
            auto_height = True
        if axwidth is not None and axheight is not None:
            auto_width = auto_height = False
        figsize = (width, height) # again
    # Automatically scale fig dimensions
    if auto_width:
        axheight = axheights*hratios[0]/sum(hratios)
        axwidth  = (axheight - hextra)*aspect + wextra # bigger w ratio, bigger width
        axwidths = axwidth*sum(wratios)/wratios[0]
        width = axwidths + left + right + sum(wspace) + rpanel_space + lpanel_space
    elif auto_height:
        axwidth = axwidths*wratios[0]/sum(wratios)
        axheight = (axwidth - wextra)/aspect + hextra
        axheights = axheight*sum(hratios)/hratios[0]
        height = axheights + top + bottom + sum(hspace) + bpanel_space
    # Check
    if axwidths<0:
        raise ValueError(f"Not enough room for axes (would have width {axwidths}). Increase width, or reduce spacings 'left', 'right', or 'wspace'.")
    if axheights<0:
        raise ValueError(f"Not enough room for axes (would have height {axheights}). Increase height, or reduce spacings 'top', 'bottom', or 'hspace'.")
    # Necessary arguments to reconstruct this grid
    subplots_kw = _dict({
        'wextra':  wextra,  'hextra':  hextra,
        'nrows':   nrows,   'ncols':   ncols,
        'figsize': figsize, 'aspect':  aspect,
        'hspace':  hspace,  'wspace':  wspace,
        'hratios': hratios, 'wratios': wratios,
        'left': left, 'bottom': bottom, 'right': right, 'top': top,
        'bwidth': bwidth, 'bspace': bspace, 'rwidth': rwidth, 'rspace': rspace, 'lwidth': lwidth, 'lspace': lspace,
        'bottompanels': bottompanels, 'leftpanels': leftpanels, 'rightpanels': rightpanels,
        })

    # Make sure the 'ratios' and 'spaces' are in physical units (we cast the
    # former to physical units), easier then to add stuff as below
    wspace = wspace.tolist()
    hspace = hspace.tolist()
    wratios = (axwidths*(wratios/sum(wratios))).tolist()
    hratios = (axheights*(hratios/sum(hratios))).tolist()
    # Now add the outer panel considerations (idea is we have panels whose
    # widths/heights are *in inches*, and only allow the main subplots and
    # figure widths/heights to warp to preserve aspect ratio)
    nrows += int(bool(bottompanels))
    ncols += int(bool(rightpanels)) + int(bool(leftpanels))
    if bottompanels:
        hratios = hratios + [bwidth]
        hspace  = hspace + [bspace]
    if leftpanels:
        wratios = [lwidth] + wratios
        wspace  = [lspace] + wspace
    if rightpanels:
        wratios = wratios + [rwidth]
        wspace  = wspace + [rspace]
    # Create gridspec for outer plotting regions (divides 'main area' from side panels)
    # Scale stuff that gridspec needs to be scaled
    # NOTE: We *no longer* scale wspace/hspace because we expect it to
    # be in same scale as axes ratios, much easier that way and no drawback really
    figsize = (width, height)
    bottom = bottom/height
    left   = left/width
    top    = 1 - top/height
    right  = 1 - right/width
    gridspec_kw = _dict({
        'nrows': nrows, 'ncols': ncols,
        'left': left, 'bottom': bottom, 'right': right, 'top': top, # so far no panels allowed here
        'wspace': wspace, 'hspace': hspace, 'width_ratios': wratios, 'height_ratios' : hratios,
        })
    return figsize, subplots_kw, gridspec_kw

def _axes_dict(naxs, value, kw=False, default=None):
    """Build a dictionary that looks like {1:value1, 2:value2, ...} or
    {1:{key1:value1, ...}, 2:{key2:value2, ...}, ...} for storing
    standardized axes-specific properties or keyword args."""
    # First build up dictionary
    # 1) 'string' or {1:'string1', (2,3):'string2'}
    if not kw:
        if np.iterable(value) and not isinstance(value, (str,dict)):
            value = {num+1: item for num,item in enumerate(value)}
        elif not isinstance(value, dict):
            value = {range(1, naxs+1): value}
    # 2) {'prop':value} or {1:{'prop':value1}, (2,3):{'prop':value2}}
    else:
        nested = [isinstance(value,dict) for value in value.values()]
        if not any(nested): # any([]) == False
            value = {range(1, naxs+1): value.copy()}
        elif not all(nested):
            raise ValueError('Pass either of dictionary of key value pairs or a dictionary of dictionaries of key value pairs.')
    # Then *unfurl* keys that contain multiple axes numbers, i.e. are meant
    # to indicate properties for multiple axes at once
    kw_out = {}
    for nums,item in value.items():
        nums = np.atleast_1d(nums)
        for num in nums.flat:
            if not kw:
                kw_out[num] = item
            else:
                kw_out[num] = item.copy()
    # Fill with default values
    for num in range(1, naxs+1):
        if num not in kw_out:
            if kw:
                kw_out[num] = {}
            else:
                kw_out[num] = default
    # Verify numbers
    if {*range(1, naxs+1)} != {*kw_out.keys()}:
        raise ValueError(f'Have {naxs} axes, but {value} has properties for axes {", ".join(str(i+1) for i in sorted(kw_out.keys()))}.')
    return kw_out

# Primary function
def subplots(array=None, ncols=1, nrows=1,
        order='C', # allow calling with subplots(array)
        emptycols=None, emptyrows=None, # obsolete?
        tight=None, outerpad=None, innerpad=None, mainpad=None,
        rcreset=True, silent=True, # arguments for figure instantiation
        span=None, # bulk apply to x/y axes
        share=None, # bulk apply to x/y axes
        spanx=1,  spany=1,  # custom setting, optionally share axis labels for axes with same xmin/ymin extents
        sharex=3, sharey=3, # for sharing x/y axis limits/scales/locators for axes with matching GridSpec extents, and making ticklabels/labels invisible
        inner=None, innerpanels=None, innercolorbars=None,
        inner_kw=None, innerpanels_kw=None, innercolorbars_kw=None,
        basemap=False, proj=None, projection=None, proj_kw=None, projection_kw=None,
        **kwargs):
    """
    Analagous to `matplotlib.pyplot.subplots`. Create a figure with a single
    axes or arbitrary grids of axes (any of which can be map projections),
    and optional "panels" along axes or figure edges.

    The parameters are sorted into the following rough sections: subplot grid
    specifications, figure and subplot sizes, axis sharing,
    inner panels, outer panels, and map projections.

    Parameters
    ----------
    ncols, nrows : int, optional
        Number of columns, rows. Ignored if `array` is not ``None``.
        Use these arguments for simpler subplot grids.
    order : {'C', 'F'}, optional
        Whether subplots are numbered in column-major (``'C'``) or row-major
        (``'F'``) order. Analagous to `numpy.array` ordering. This controls
        the order axes appear in the `axs` list, and the order of a-b-c
        labelling when using `~proplot.axes.BaseAxes.format` with ``abc=True``.
    array : None or array-like of int, optional
        2-dimensional array specifying complex grid of subplots. Think of
        this array as a "picture" of your figure. For example, the array
        ``[[1, 1], [2, 3]]`` creates one long subplot in the top row, two
        smaller subplots in the bottom row.

        Integers must range from 1 to the number of plots. For example,
        ``[[1, 4]]`` is invalid.

        ``0`` indicates an empty space. For example, ``[[1, 1, 1], [2, 0, 3]]``
        creates one long subplot in the top row with two subplots in the bottom
        row separated by a space.
    emptyrows, emptycols : None or list of int, optional
        Row, column numbers (starting from 1) that you want empty. Generally
        this is used with `ncols` and `nrows`; with `array`, you can
        just use zeros.

    width, height : float or str, optional
        The figure width and height. If float, units are inches. If string,
        units are interpreted by `~proplot.utils.units` -- for example,
        `width="10cm"` creates a 10cm wide figure.
    figsize : length-2 tuple, optional
        Tuple specifying the figure `(width, height)`.
    journal : None or str, optional
        Conform figure width (and height, if specified) to academic journal
        standards. See `~proplot.gridspec.journal_size` for details.
    axwidth, axheight : float or str, optional
        Sets the average width, height of your axes. If float, units are
        inches. If string, units are interpreted by `~proplot.utils.units`.

        These arguments are convenient where you don't care about the figure
        dimensions and just want your axes to have enough "room".
    aspect : float or length-2 list of floats, optional
        The (average) axes aspect ratio, in numeric form (width divided by
        height) or as (width, height) tuple. If you do not provide
        the `hratios` or `wratios` keyword args, all axes will have
        identical aspect ratios.
    hspace, wspace, hratios, wratios, left, right, top, bottom : optional
        Passed to `~proplot.gridspec.FlexibleGridSpecBase`.

    sharex, sharey, share : {3, 2, 1, 0}, optional
        The "axis sharing level" for the *x* axis, *y* axis, or both
        axes. Options are as follows:

            0. No axis sharing.
            1. Only draw *axis label* on the leftmost column (*y*) or
               bottommost row (*x*) of subplots. Axis tick labels
               still appear on every subplot.
            2. As in 1, but forces the axis limits to be identical. Axis
               tick labels still appear on every subplot.
            3. As in 2, but only show the *axis tick labels* on the
               leftmost column (*y*) or bottommost row (*x*) of subplots.

        This feature can considerably redundancy in your figure.
    spanx, spany, span : bool or {1, 0}, optional
        Whether to use "spanning" axis labels for the *x* axis, *y* axis, or
        both axes. When ``True`` or ``1``, the axis label for the leftmost
        (*y*) or bottommost (*x*) subplot is **centered** on that column or
        row -- i.e. it "spans" that column or row. For example, this means
        for a 3-row figure, you only need 1 y-axis label instead of 3.

        This feature can considerably redundancy in your figure.

        "Spanning" labels also integrate with "shared" axes. For example,
        for a 3-row, 3-column figure, with ``sharey>1`` and ``spany=1``,
        your figure will have **1** *y*-axis label instead of 9.

    inner
        Alias for `innerpanels`.
    innerpanels : str or dict-like, optional
        Specify which axes should have inner "panels".

        Panels are stored on the ``leftpanel``, ``rightpanel``,
        ``bottompanel`` and ``toppanel`` attributes on the axes instance.
        You can also use the aliases ``lpanel``, ``rpanel``, ``bpanel``,
        or ``tpanel``.

        The argument is interpreted as follows:

            * If str, panels are drawn on the same side for all subplots.
              String should contain any of the characters ``'l'`` (left panel),
              ``'r'`` (right panel), ``'t'`` (top panel), or ``'b'`` (bottom panel).
              For example, ``'rt'`` indicates a right and top panel.
            * If dict-like, panels can be drawn on different sides for
              different subplots. For example, `innerpanels={1:'r', (2,3):'l'}`
              indicates that we want to draw a panel on the right side of
              subplot number 1, on the left side of subplots 2 and 3.

    innercolorbars : str or dict-like, optional
        Identical to ``innerpanels``, except the default panel size is more
        appropriate for a colorbar. The panel can then be **filled** with
        a colorbar with, for example, ``ax.leftpanel.colorbar(...)``.
    inner_kw, innercolorbars_kw
        Aliases for `innerpanels_kw`.
    innerpanels_kw : dict-like, optional
        Keyword args passed to `~Figure.panel_factory`. Controls
        the width/height and spacing of panels.

        Can be dict of properties (applies globally), or **dict of dicts** of
        properties (applies to specific properties, as with `innerpanels`).

        For example, consider a figure with 2 columns and 1 row.
        With ``{'lwidth':1}``, all left panels 1 inch wide, while
        with ``{1:{'lwidth':1}, 2:{'lwidth':0.5}}``, the left subplot
        panel is 1 inch wide and the right subplot panel is 0.5 inches wide.

        See `~Figure.panel_factory` for keyword arg options.

    bottompanel, rightpanel, leftpanel : bool, optional
        Whether to draw an "outer" panel surrounding the entire grid of
        subplots, on the bottom, right, or left sides of the figure.
        This is great for creating global colorbars and legends.
        Default is ``False``.

        Panels are stored on the ``leftpanel``, ``rightpanel``,
        and ``bottompanel`` attributes on the figure instance.
        You can also use the aliases ``lpanel``, ``rpanel``, and ``bpanel``.
    bottompanels, rightpanels, leftpanels : bool or list of int, optional
        As with the `panel` keyword args, but this allows you to
        specify **multiple** panels along each figure edge.
        Default is ``False``.

        The arguments are interpreted as follows:

            * If bool, separate panels **for each column/row of subplots**
              are drawn.
            * If list of int, can specify panels that span **contiguous
              columns/rows**. Usage is similar to the ``array`` argument.

              For example, for a figure with 3 columns, ``bottompanels=[1, 2, 2]``
              draws a panel on the bottom of the first column and spanning the
              bottom of the right 2 columns, and ``bottompanels=[0, 2, 2]``
              only draws a panel underneath the right 2 columns (as with
              `array`, the ``0`` indicates an empty space).

    bottomcolorbar, bottomcolorbars, rightcolorbar,  rightcolorbars, leftcolorbar, leftcolorbars
        Identical to the corresponding ``panel`` and ``panels`` keyword
        args, except the default panel size is more appropriate for a colorbar.

        The panel can then be **filled** with a colorbar with, for example, 
        ``fig.leftpanel.colorbar(...)``.
    bottomlegend, bottomlegends, rightlegend,  rightlegends, leftlegend, leftlegends
        Identical to the corresponding ``panel`` and ``panels`` keyword
        args, except the default panel size is more appropriate for a legend.

        The panel can then be **filled** with a legend with, for example, 
        ``fig.leftpanel.legend(...)``.
    bpanel, bpanels, bcolorbar, bcolorbars, blegend, blegends, rpanel, rpanels, rcolorbar, rcolorbars, rlegend, rlegends, lpanel, lpanels, lcolorbar, lcolorbars, llegend, llegends
        Aliases for equivalent args with ``bottom``, ``right``, and ``left``.
        It's a bit faster, so why not?
    lwidth, rwidth, bwidth, twidth : float, optional
        Width of left, right, bottom, and top panels. If float, units are
        inches. If string, units are interpreted by `~proplot.utils.units`.
    lspace, rspace, bspace, tspace : float, optional
        Space between the "inner" edges of the left, right, bottom, and top
        panels and the edge of the main subplot grid. If float, units are
        inches. If string, units are interpreted by `~proplot.utils.units`.

    proj, proj_kw
        Aliases for `projection`, `projection_kw`.
    projection : str or dict-like, optional
        The map projection name.

        If string, applies to all subplots. If dictionary, can be
        specific to each subplot, as with `innerpanels`.

        For example, consider a figure with 4 columns and 1 row.
        With ``projection={1:'mercator', (2,3):'hammer'}``,
        the leftmost subplot is a Mercator projection, the middle 2 are Hammer
        projections, and the rightmost is a normal x/y axes.
    projection_kw : dict-like, optional
        Keyword arguments passed to `~mpl_toolkits.basemap.Basemap` or
        cartopy `~cartopy.crs.Projection` class on instantiation.

        As with `innerpanels_kw`,
        can be dict of properties (applies globally), or **dict of dicts** of
        properties (applies to specific properties, as with `innerpanels`).
    basemap : bool or dict-like, optional
        Whether to use `~mpl_toolkits.basemap.Basemap` or
        `~cartopy.crs.Projection` for map projections. Defaults to ``False``.

        If boolean, applies to all subplots. If dictionary, can be
        specific to each subplot, as with `innerpanels`.

    Returns
    -------
    f : `Figure`
        The figure instance.
    axs : `axes_list`
        A special list of axes instances. See `axes_list`.

    Other parameters
    ----------------
    tight, pad, innerpad, rcreset, silent
        Passed to `Figure`.
    **kwargs
        Passed to `~proplot.gridspec.FlexibleGridSpec`.

    See also
    --------
    `~proplot.axes.BaseAxes`, `~proplot.axes.BaseAxes.format`

    Notes
    -----
    * Matplotlib `~matplotlib.axes.Axes.set_aspect` option seems to behave
      strangely for some plots; for this reason we override the ``fix_aspect``
      keyword arg provided by `~mpl_toolkits.basemap.Basemap` and just draw
      the figure with appropriate aspect ratio to begin with. Otherwise, we
      get weird differently-shaped subplots that seem to make no sense.
    * All shared axes will generally end up with the same axis
      limits, scaling, major locators, and minor locators. The
      ``sharex`` and ``sharey`` detection algorithm really is just to get
      instructions to make the tick labels and axis labels **invisible**
      for certain axes.

    Todo
    ----
    * Add options for e.g. `bpanel` keyword args.
    * Fix axes aspect ratio stuff when width/height ratios are not one!
    * Generalize axes sharing for right y-axes and top x-axes. Enable a secondary
      axes sharing mode where we *disable ticklabels and labels*, but *do not
      use the builtin sharex/sharey API*, suitable for complex map projections.
    * For spanning axes labels, right now only detect **x labels on bottom**
      and **ylabels on top**. Generalize for all subplot edges.
    """
    #--------------------------------------------------------------------------#
    # Initial stuff
    #--------------------------------------------------------------------------#
    # Array setup
    rc._getitem_mode = 0 # ensure still zero; might be non-zero if had error in 'with context' block
    if array is None:
        array = np.arange(1,nrows*ncols+1)[...,None]
        if order not in ('C','F'): # better error message
            raise ValueError(f'Invalid order "{order}". Choose from "C" (row-major, default) and "F" (column-major).')
        array = array.reshape((nrows, ncols), order=order) # numpy is row-major, remember
    array = np.array(array) # enforce array type
    if array.ndim==1:
        if order not in ('C','F'): # better error message
            raise ValueError(f'Invalid order "{order}". Choose from "C" (row-major, default) and "F" (column-major).')
        array = array[None,:] if order=='C' else array[:,None] # interpret as single row or column
    # Empty rows/columns feature
    array[array==None] = 0 # use zero for placeholder; otherwise have issues
    if emptycols:
        emptycols = np.atleast_1d(emptycols)
        for col in emptycols.flat:
            array[:,col-1] = 0
    if emptyrows:
        emptyrows = np.atleast_1d(emptyrows)
        for row in emptyrows.flat:
            array[row-1,:] = 0
    # Enforce rule
    nums = np.unique(array[array!=0])
    naxs = len(nums)
    if {*nums.flat} != {*range(1, naxs+1)}:
        raise ValueError('Axes numbers must span integers 1 to naxs (i.e. cannot skip over numbers).')
    nrows = array.shape[0]
    ncols = array.shape[1]
    # Parse outer panel kwargs, consolidate settings
    kwargs = _parse_panels(nrows, ncols, **kwargs)

    # Figure out rows and columns "spanned" by each axes in list, for
    # axis sharing and axis label spanning settings
    sharex = int(_default(share, sharex))
    sharey = int(_default(share, sharey))
    spanx  = _default(span, spanx)
    spany  = _default(span, spany)
    if sharex not in range(4) or sharey not in range(4):
        raise ValueError('Axis sharing options sharex/sharey can be 0 (no sharing), 1 (sharing, but keep all tick labels), and 2 (sharing, but only keep one set of tick labels).')
    # Get some axes properties. Locations are sorted by axes id.
    axids = [np.where(array==i) for i in np.sort(np.unique(array)) if i>0] # 0 stands for empty
    offset = (0, int(bool(kwargs['leftpanels'])))
    yrange = offset[0] + np.array([[y.min(), y.max()+1] for y,_ in axids]) # yrange is shared columns
    xrange = offset[1] + np.array([[x.min(), x.max()+1] for _,x in axids])
    # Shared axes: Generate list of base axes-dependent axes pairs
    # That is, find where the minimum-maximum gridspec extent in 'x' for a
    # given axes matches the minimum-maximum gridspec extent for a base axes.
    xgroups_base, xgroups, grouped = [], [], {*()}
    if sharex:
        for i in range(naxs): # axes now have pseudo-numbers from 0 to naxs-1
            matches       = (xrange[i,:]==xrange).all(axis=1) # *broadcasting rules apply here*
            matching_axes = np.where(matches)[0] # gives ID number of matching_axes, from 0 to naxs-1
            if i not in grouped and matching_axes.size>1:
                # Find all axes that have the same gridspec 'x' extents
                xgroups += [matching_axes]
                # Get bottom-most axis with shared x; should be single number
                xgroups_base += [matching_axes[np.argmax(yrange[matching_axes,1])]]
            grouped.update(matching_axes) # bookkeeping; record ids that have been grouped already
    ygroups_base, ygroups, grouped = [], [], {*()}
    # Similar for *y* axis
    if sharey:
        for i in range(naxs):
            matches       = (yrange[i,:]==yrange).all(axis=1) # *broadcasting rules apply here*
            matching_axes = np.where(matches)[0]
            if i not in grouped and matching_axes.size>1:
                ygroups += [matching_axes]
                ygroups_base += [matching_axes[np.argmin(xrange[matching_axes,0])]] # left-most axis with shared y, for matching_axes
            grouped.update(matching_axes) # bookkeeping; record ids that have been grouped already

    # Get basemap.Basemap or cartopy.CRS instances for map, and
    # override aspect ratio.
    # NOTE: Cannot have mutable dict as default arg, because it changes the
    # "default" if user calls function more than once! Swap dicts for None.
    basemap = _axes_dict(naxs, basemap, kw=False, default=False)
    proj    = _axes_dict(naxs, _default(proj, projection, 'xy'), kw=False, default='xy')
    proj_kw = _axes_dict(naxs, _default(proj_kw, projection_kw, {}), kw=True)
    axes_kw = {num:{} for num in range(1, naxs+1)}  # stores add_subplot arguments
    for num,name in proj.items():
        # The default, my XYAxes projection
        if name=='xy':
            axes_kw[num]['projection'] = 'xy'
        # Builtin matplotlib polar axes, just use my overridden version
        elif name=='polar':
            axes_kw[num]['projection'] = 'newpolar'
            if num==1:
                kwargs.update(aspect=1)
        # Custom Basemap and Cartopy axes
        elif name:
            package = 'basemap' if basemap[num] else 'cartopy'
            instance, aspect, kwproj = projs.Proj(name, basemap=basemap[num], **proj_kw[num])
            if num==1:
                kwargs.update({'aspect':aspect})
            axes_kw[num].update({'projection':package, 'map_projection':instance})
            axes_kw[num].update(kwproj)
        else:
            raise ValueError('All projection names should be declared. Wut.')

    # Create dictionary of panel toggles and settings
    # Input can be string e.g. 'rl' or dictionary e.g. {(1,2,3):'r', 4:'l'}
    innerpanels    = _axes_dict(naxs, _default(inner, innerpanels, ''), kw=False, default='')
    innercolorbars = _axes_dict(naxs, _default(innercolorbars, ''), kw=False, default='')
    innerpanels_kw = _axes_dict(naxs, _default(inner_kw, innercolorbars_kw, innerpanels_kw, {}), kw=True)
    # Check input, make sure user didn't use boolean or anything
    if not isinstance(innercolorbars, (dict, str)):
        raise ValueError('Must pass string of panel sides or dictionary mapping axes numbers to sides.')
    if not isinstance(innerpanels, (dict, str)):
        raise ValueError('Must pass string of panel sides or dictionary mapping axes numbers to sides.')
    # Apply, with changed defaults for inner colorbars
    # Also set the wspace, hspace keyword args, so we can calculate the extra
    # space occupied by panels in order to adjust aspect ratio calculation
    translate = lambda p: {'bottom':'b', 'top':'t', 'right':'r', 'left':'l'}.get(p, p)
        # Get which panels
    for idx in range(naxs):
        num = idx + 1
        kw = innerpanels_kw[num]
        which1 = translate(innerpanels[num])
        which2 = translate(innercolorbars[num])
        if {*which1} & {*which2}:
            raise ValueError('You requested same side for inner colorbar and inner panel.')
        which = which1 + which2
        kw['which'] = which
        # Get widths, easy
        # TODO: Change rc setting names
        for side in 'lrtb':
            width = f'{side}width' # lwidth, rwidth, etc.
            if side in which2:
                kw[f'{side}share'] = False
            if width in kw:
                continue
            if side in which1:
                kw[width] = rc['gridspec.panelwidth']
            elif side in which2:
                kw[width] = rc['gridspec.cbarwidth']
            else:
                kw.pop(width, None) # pull out! only specify width if panel present, used in wextra/hextra calculation
        # Get spaces, a bit more tricky
        for space,regex,cspaces in zip(('hspace','wspace'), ('tb', 'lr'), (('nolab','ylab'), ('xlab','nolab'))):
            cwhich = re.sub(f'[^{regex}]', '', which2)
            pwhich = re.sub(f'[^{regex}]', '', which1)
            if space in kw:
                if not np.iterable(kw[space]):
                    kw[space] = [kw[space]]*(len(cwhich) + len(pwhich))
                continue
            rcspace = [rc['gridspec.panelspace']]*(len(cwhich) + len(pwhich))
            if regex[0] in cwhich:
                rcspace[0] = rc[f'gridspec.{cspaces[0]}']
            if regex[1] in cwhich:
                rcspace[-1] = rc[f'gridspec.{cspaces[1]}']
            kw[space] = rcspace

    # Make sure same panels are present on ***all rows and columns***; e.g.
    # if left panel was requested for top subplot in 2-row figure, will
    # allocate space for empty left panel in bottom subplot too, to keep
    # things aligned! Below func modified underlying mutable dicts, does not
    # need to return anything.
    def sync(filt, side):
        filt_kw = {idx+1: innerpanels_kw[idx+1] for idx in filt}
        side_kw = {key:kw for key,kw in filt_kw.items() if side in kw['which']}
        if not side_kw:
            return
        spc = {'t':'h', 'b':'h', 'l':'w', 'r':'w'}[side] + 'space'
        idx = {'t':0, 'b':-1, 'l':0, 'r':-1}[side]
        space = max(kw[spc][idx] for kw in side_kw.values())
        width = max(kw[f'{side}width'] for kw in side_kw.values())
        for kw in filt_kw.values():
            kw[f'{side}width'] = width
            if side in kw['which']:
                kw[spc][idx] = space # enforce, e.g. in case one left panel has different width compared to another!
            else:
                kw[f'{side}visible'] = False
                kw['which'] += side
                if side in 'tl':
                    kw[spc] = [space, *kw[spc]]
                else:
                    kw[spc] = [*kw[spc], space]
    # Top, bottom
    for row in range(nrows):
        filt, = np.where(yrange[:,0]==row) # note ranges are *slices*, not endpoint inclusive
        sync(filt, 't')
        filt, = np.where((yrange[:,1]-1)==row) # note ranges are *slices*, not endpoint inclusive
        sync(filt, 'b')
    # Left, right
    for col in range(ncols):
        filt, = np.where(xrange[:,0]==col) # note ranges are *slices*, not endpoint inclusive
        sync(filt, 'l')
        filt, = np.where((xrange[:,1]-1)==col) # note ranges are *slices*, not endpoint inclusive
        sync(filt, 'r')
    # Fix subplots_kw aspect ratio in light of inner panels
    kw = innerpanels_kw[1]
    kwargs['wextra'] = sum(units(w) for w in kw['wspace']) + \
        sum(units(kw.get(f'{side}width', 0)) for side in 'lr')
    kwargs['hextra'] = sum(units(h) for h in kw['hspace']) + \
        sum(units(kw.get(f'{side}width', 0)) for side in 'tb')

    # Parse arguments, figure out axes dimensions, initialize gridspec
    figsize, subplots_kw, gridspec_kw = _parse_args(nrows, ncols, **kwargs)
    gs  = gridspec.FlexibleGridSpec(**gridspec_kw)
    # Create figure, axes, and outer panels
    axs = naxs*[None] # list of axes
    fig = plt.figure(gridspec=gs, figsize=figsize, FigureClass=Figure,
        tight=tight, outerpad=outerpad, mainpad=mainpad, innerpad=innerpad,
        rcreset=rcreset, silent=silent,
        subplots_kw=subplots_kw)
    for idx in range(naxs):
        num = idx + 1
        ax_kw = axes_kw[num]
        if innerpanels_kw[num]['which']: # non-empty
            axs[idx] = fig.panel_factory(gs[slice(*yrange[idx,:]), slice(*xrange[idx,:])],
                    number=num, spanx=spanx, spany=spany, **ax_kw, **innerpanels_kw[num])
        else:
            axs[idx] = fig.add_subplot(gs[slice(*yrange[idx,:]), slice(*xrange[idx,:])],
                    number=num, spanx=spanx, spany=spany, **ax_kw) # main axes can be a cartopy projection
    def add_panel(side):
        panels = subplots_kw[f'{side}panels']
        if not panels:
            return
        paxs = []
        for n in np.unique(panels).flat:
            off = offset[0] if side in ('left','right') else offset[1]
            idx, = np.where(panels==n)
            idx = slice(off + min(idx), off + max(idx) + 1)
            if side=='right':
                subspec = gs[idx,-1]
            elif side=='left':
                subspec = gs[idx,0]
            elif side=='bottom':
                subspec = gs[-1,idx]
            pax = fig.add_subplot(subspec, side=side, invisible=False, projection='panel')
            paxs += [pax]
        setattr(fig, f'{side}panel', axes_list(paxs))
    add_panel('bottom')
    add_panel('right')
    add_panel('left')

    # Set up axis sharing
    # NOTE: You must loop through twice, separately for x and y sharing. Fails
    # otherwise, don't know why.
    if sharex:
        for idx in range(naxs):
            igroup = np.where([idx in g for g in xgroups])[0]
            if igroup.size!=1:
                continue
            sharex_ax = axs[xgroups_base[igroup[0]]]
            if axs[idx] is sharex_ax:
                continue
            axs[idx]._sharex_setup(sharex_ax, sharex)
    if sharey:
        for idx in range(naxs):
            igroup = np.where([idx in g for g in ygroups])[0] # np.where works on lists
            if igroup.size!=1:
                continue
            sharey_ax = axs[ygroups_base[igroup[0]]]
            if axs[idx] is sharey_ax:
                continue
            axs[idx]._sharey_setup(sharey_ax, sharey)
    for ax in axs: # check axes don't belong to multiple groups, should be impossible unless my code is completely wrong...
        for name,groups in zip(('sharex', 'sharey'), (xgroups, ygroups)):
            if sum(ax in group for group in xgroups)>1:
                raise ValueError(f'Something went wrong; axis {idx:d} belongs to multiple {name} groups.')

    # Return results
    fig.main_axes = axs
    return fig, axes_list(axs)

