"""Read-only implementation policy and server-authored meshing request facts."""
CONTROL_SHAPE=(33,33)
PERCENTILE_POSITIONS=(25,95)
RELIEF_GAMMA=2
GMSH_ALGORITHM=5


def raster_roi_type(path):
    from pathlib import Path
    return 'ascii_grid' if Path(path).suffix.lower()=='.asc' else 'geotiff'


def policy():
    return dict(policy_version='meshing-policy-v1',control_shape=list(CONTROL_SHAPE),
        percentile_positions=list(PERCENTILE_POSITIONS),gamma=RELIEF_GAMMA,
        relief_statistic='local maximum minus minimum',sizing_resampling='nearest',
        neighbourhood='rectangular control-grid window; ceil(R/dx),ceil(R/dy), clipped to 1..32',
        missing_target='h_max',constant_relief_target='h_max',gradation_limiter='none',
        gmsh_algorithm=GMSH_ALGORITHM,mesh_size_factor=1,
        point_sizing=False,curvature_sizing=False,boundary_extension=False,
        shp_transform_subdivisions=8)


def request_summary(config, domain_evidence=None):
    source=config['cell_source'];kind=source['kind'];crs=config['analysis_crs']
    summary=dict(mesh_kind=kind,analysis_crs=crs,max_cells=source.get('max_cells',1000),
        cell_limit_role='safety cap, not target count')
    if kind=='gmsh_generated':
        roi_type='shapefile' if source.get('boundary_path') else raster_roi_type(source['roi_raster_path']) if source.get('roi_raster_path') else 'rectangle'
        summary.update(mode=source['sizing'],roi_type=roi_type,
            roi_source=source.get('boundary_path') or source.get('roi_raster_path') or source.get('extent'),
            requested_target_min_m=source['h_min_m'],requested_target_max_m=source['h_max_m'])
        evidence=domain_evidence or {}
        summary['source_crs_wkt']=evidence.get('source_crs_wkt',crs)
        from rasterio.crs import CRS
        summary['source_crs']=str(CRS.from_user_input(summary['source_crs_wkt']))
        summary['roi_subdivisions']=8 if roi_type=='shapefile' else config.get('edge_subdivisions',8) if roi_type in {'geotiff','ascii_grid'} else None
        if roi_type=='shapefile':
            summary['boundary_vertices']=dict(source=evidence.get('source_vertices'),
                transformed=evidence.get('transformed_vertices'),simplification_applied=False)
            summary['boundary_warnings']=evidence.get('warnings',[])
        if source['sizing']=='uniform':summary['requested_target_size_m']=source['h_max_m']
        elif source['sizing']=='sf1_relief':
            from .sf1_sizing import policy as sf1_policy
            summary.update(dem_source=source['dem_raster_path'],dem_band=source['dem_band'],dem_value_units=source['dem_value_units'],
                sf1_scale=source['sf1_scale'],fixed_policy=sf1_policy())
        else:summary.update(dem_source=source.get('dem_raster_path',config.get('raster_path')),
            dem_band=source.get('dem_band',config.get('band',1)),radius_m=source['relief_radius_m'],fixed_policy=policy())
    elif kind=='structured_grid':
        summary.update(mode='structured',cell_width_m=source['cell_width_m'],cell_height_m=source['cell_height_m'],
            boundary_policy=source['boundary_policy'],roi_type=raster_roi_type(source['roi_raster_path']) if source.get('roi_raster_path') else 'rectangle',
            roi_source=source.get('roi_raster_path') or source['extent'])
    elif kind=='mesh_artifact':summary.update(mode='imported',roi_source=source['path'],geometry_authority='imported cells')
    if config.get('schema_version')=='gis-workbench-run-config-v2':
        from .raster_resolution import planning_reference
        summary['resolution_reference']=planning_reference(config)
        if kind=='gmsh_generated' and source['sizing'] in {'relief','sf1_relief'}:
            from .raster_resolution import read
            summary['dem_resolution']=read(source['dem_raster_path'],crs,source['dem_band'])
    return summary
