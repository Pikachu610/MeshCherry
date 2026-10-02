"""Inspectable figures for the real terrain-sized mesh and cell graph."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection


def _segments(vertices, triangles):
    vertices = np.asarray(vertices)[:, :2]
    edges = set()
    for tri in triangles:
        for a, b in ((tri[0],tri[1]),(tri[1],tri[2]),(tri[2],tri[0])):
            edges.add(tuple(sorted((a,b))))
    return np.asarray([[vertices[a], vertices[b]] for a,b in sorted(edges)])


def render_adaptive_figure(path, terrain, final_h, vertices, triangles, graph, label):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    vertices_np = np.asarray(vertices)
    triangles_np = np.asarray(triangles)
    centers = vertices_np[triangles_np, :2].mean(axis=1)
    segments = _segments(vertices, triangles)
    valid = np.where(terrain.valid, terrain.z, np.nan)
    extent = [terrain.transform.c,
              terrain.transform.c + terrain.z.shape[1]*terrain.transform.a,
              terrain.transform.f + terrain.z.shape[0]*terrain.transform.e,
              terrain.transform.f]
    high_index = np.nanargmax(np.where(terrain.valid, terrain.scores['combined'], np.nan))
    row, col = np.unravel_index(high_index, terrain.z.shape)
    cx = terrain.transform.c + (col + .5)*terrain.transform.a
    cy = terrain.transform.f + (row + .5)*terrain.transform.e
    radius = 2200
    fig, axes = plt.subplots(2, 2, figsize=(15, 12), constrained_layout=True)
    im = axes[0,0].imshow(valid, extent=extent, origin='upper', cmap='terrain')
    axes[0,0].set_title('DEM reference (m)')
    fig.colorbar(im, ax=axes[0,0], shrink=.75)
    im = axes[0,1].imshow(np.where(terrain.valid, final_h, np.nan), extent=extent, origin='upper', cmap='viridis_r', vmin=250, vmax=1000)
    axes[0,1].set_title('Final spatial target h (m)')
    fig.colorbar(im, ax=axes[0,1], shrink=.75)
    axes[1,0].add_collection(LineCollection(segments, colors='#223344', linewidths=.16, alpha=.8))
    axes[1,0].autoscale(); axes[1,0].set_aspect('equal')
    axes[1,0].set_title(f'Actual ROI4 triangular mesh — {len(triangles):,} cells')
    inside = ((segments[:,:,0] >= cx-radius) & (segments[:,:,0] <= cx+radius) &
              (segments[:,:,1] >= cy-radius) & (segments[:,:,1] <= cy+radius)).any(axis=1)
    axes[1,1].add_collection(LineCollection(segments[inside], colors='#8c9aa6', linewidths=.55))
    center_inside = ((centers[:,0]>=cx-radius)&(centers[:,0]<=cx+radius)&
                     (centers[:,1]>=cy-radius)&(centers[:,1]<=cy+radius))
    # Graph adjacency is drawn between cell-centre nodes, distinct from mesh edges.
    pairs = []
    for interface in graph['interfaces']:
        if len(interface['cell_ids']) == 2:
            a,b = (int(value.split('-')[1]) for value in interface['cell_ids'])
            if center_inside[a] or center_inside[b]:
                pairs.append([centers[a], centers[b]])
    if pairs:
        axes[1,1].add_collection(LineCollection(np.asarray(pairs), colors='#e05a47', linewidths=.5, alpha=.65))
    axes[1,1].scatter(centers[center_inside,0], centers[center_inside,1], s=7, c='#0b559f', zorder=3, label='cell-centred graph node')
    axes[1,1].set_xlim(cx-radius,cx+radius); axes[1,1].set_ylim(cy-radius,cy+radius)
    axes[1,1].set_aspect('equal'); axes[1,1].legend(loc='upper right', fontsize=8)
    axes[1,1].set_title('Zoom: mesh edges (grey), nodes (blue), graph adjacency (red)')
    for axis in axes.ravel():
        axis.set_xlabel('Easting (m)'); axis.set_ylabel('Northing (m)')
    fig.suptitle(label + '\nPlanar mesh; terrain controls size and is attached as attributes', fontsize=14)
    fig.savefig(path, dpi=190)
    plt.close(fig)
