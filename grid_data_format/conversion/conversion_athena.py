import os
import weakref
import numpy as na
import h5py as h5
from conversion_abc import *
from glob import glob
from collections import \
    defaultdict
from string import \
    strip, \
    rstrip
from stat import \
    ST_CTIME

translation_dict = {}
translation_dict['density'] = 'density'
translation_dict['total_energy'] = 'specific_energy'
translation_dict['velocity_x'] = 'velocity_x'
translation_dict['velocity_y'] = 'velocity_y'
translation_dict['velocity_z'] = 'velocity_z'
translation_dict['cell_centered_B_x'] = 'mag_field_x'
translation_dict['cell_centered_B_y'] = 'mag_field_y'
translation_dict['cell_centered_B_z'] = 'mag_field_z'

class AthenaDistributedConverter(Converter):
    def __init__(self, basename, outname=None, field_conversions=None):
        self.fields = []
        self.current_time=0.0
        name = basename.split('.')
        self.ddn = int(name[1])
        self.basename = name[0]
        if outname is None:
            outname = self.basename+'.%04i'%self.ddn+'.gdf'
        self.outname = outname
	if field_conversions is None:
	    field_conversions = {}
	self.field_conversions = field_conversions


    def parse_line(self,line, grid):
    #    print line
        # grid is a dictionary
        splitup = line.strip().split()
        if "vtk" in splitup:
            grid['vtk_version'] = splitup[-1]
        elif "Really" in splitup:
            grid['time'] = splitup[-1]
            self.current_time = grid['time']
        elif 'PRIMITIVE' in splitup:
            grid['time'] = float(splitup[4].rstrip(','))
            grid['level'] = int(splitup[6].rstrip(','))
            grid['domain'] = int(splitup[8].rstrip(','))
            self.current_time = grid['time']
        elif "DIMENSIONS" in splitup:
            grid['dimensions'] = na.array(splitup[-3:]).astype('int')
        elif "ORIGIN" in splitup:
            grid['left_edge'] = na.array(splitup[-3:]).astype('float64')
        elif "SPACING" in splitup:
            grid['dds'] = na.array(splitup[-3:]).astype('float64')
        elif "CELL_DATA" in splitup:
            grid["ncells"] = int(splitup[-1])
        elif "SCALARS" in splitup:
            field = splitup[1]
            grid['read_field'] = field
            grid['read_type'] = 'scalar'
        elif "VECTORS" in splitup:
            field = splitup[1]
            grid['read_field'] = field
            grid['read_type'] = 'vector'

    def write_gdf_field(self, fn, grid_number, field, data):
        f = h5.File(fn,'a')
        ## --------- Store Grid Data --------- ##
        if 'grid_%010i'%grid_number not in f['data'].keys():
            g = f['data'].create_group('grid_%010i'%grid_number)
        else:
            g = f['data']['grid_%010i'%grid_number]
        name = field
        try:
            name = translation_dict[name]
        except:
            pass
        # print 'Writing %s' % name
        if not name in g.keys(): 
            g.create_dataset(name,data=data)
        f.close()


    def read_and_write_hierarchy(self,basename, ddn, gdf_name):
        """ Read Athena legacy vtk file from multiple cpus """
        proc_names = glob('id*')
        print 'Reading a dataset from %i Processor Files' % len(proc_names)
        N = len(proc_names)
        grid_dims = na.empty([N,3],dtype='int64')
        grid_left_edges = na.empty([N,3],dtype='float64')
        grid_dds = na.empty([N,3],dtype='float64')
        grid_levels = na.zeros(N,dtype='int64')
        grid_parent_ids = -1*na.ones(N,dtype='int64')
        grid_particle_counts = na.zeros([N,1],dtype='int64')

        for i in range(N):
            if i == 0:
                fn = 'id%i/'%i + basename + '.%04i'%ddn + '.vtk'
            else:
                fn = 'id%i/'%i + basename + '-id%i'%i + '.%04i'%ddn + '.vtk'

            print 'Reading file %s' % fn
            f = open(fn,'rb')
            grid = {}
            grid['read_field'] = None
            grid['read_type'] = None
            table_read=False
            line = f.readline()
            while grid['read_field'] is None:
                self.parse_line(line, grid)
                if "SCALAR" in line.strip().split():
                    break
                if "VECTOR" in line.strip().split():
                    break
                if 'TABLE' in line.strip().split():
                    break
                if len(line) == 0: break
                line = f.readline()

            if len(line) == 0: break
            if na.prod(grid['dimensions']) != grid['ncells']:
                grid['dimensions'] -= 1
            if na.prod(grid['dimensions']) != grid['ncells']:
                print 'product of dimensions %i not equal to number of cells %i' % \
                      (na.prod(grid['dimensions']), grid['ncells'])
                raise TypeError

            # Append all hierachy info before reading this grid's data
            grid_dims[i]=grid['dimensions']
            grid_left_edges[i]=grid['left_edge']
            grid_dds[i]=grid['dds']
            #grid_ncells[i]=grid['ncells']
            del grid

            f.close()

        f = h5.File(gdf_name,'a')

        ## --------- Begin level nodes --------- ##
        g = f.create_group('gridded_data_format')
        g.attrs['format_version']=na.float32(1.0)
        g.attrs['data_software']='athena'
        data_g = f.create_group('data')
        field_g = f.create_group('field_types')
        part_g = f.create_group('particle_types')
        pars_g = f.create_group('simulation_parameters')


        gles = grid_left_edges
        gdims = grid_dims
        dle = na.min(gles,axis=0)
        dre = na.max(gles+grid_dims*grid_dds,axis=0)
        glis = ((gles - dle)/grid_dds).astype('int64')
        gris = glis + gdims

        ddims = (dre-dle)/grid_dds[0]

        # grid_left_index
        gli = f.create_dataset('grid_left_index',data=glis)
        # grid_dimensions
        gdim = f.create_dataset('grid_dimensions',data=gdims)

        # grid_level
        level = f.create_dataset('grid_level',data=grid_levels)

        ## ----------QUESTIONABLE NEXT LINE--------- ##
        # This data needs two dimensions for now. 
        part_count = f.create_dataset('grid_particle_count',data=grid_particle_counts)

        # grid_parent_id
        pids = f.create_dataset('grid_parent_id',data=grid_parent_ids)

        ## --------- Done with top level nodes --------- ##

        pars_g.attrs['refine_by'] = na.int64(1)
        pars_g.attrs['dimensionality'] = na.int64(3)
        pars_g.attrs['domain_dimensions'] = ddims
        pars_g.attrs['current_time'] = self.current_time
        pars_g.attrs['domain_left_edge'] = dle
        pars_g.attrs['domain_right_edge'] = dre
        pars_g.attrs['unique_identifier'] = 'athenatest'
        pars_g.attrs['cosmological_simulation'] = na.int64(0)
        pars_g.attrs['num_ghost_zones'] = na.int64(0)
        pars_g.attrs['field_ordering'] = na.int64(1)
        pars_g.attrs['boundary_conditions'] = na.int64([0]*6) # For Now

        # Extra pars:
        # pars_g.attrs['n_cells'] = grid['ncells']
        pars_g.attrs['vtk_version'] = 1.0

        # Add particle types
        # Nothing to do here

        # Add particle field attributes
        f.close()


    def read_and_write_data(self, basename, ddn, gdf_name):
        proc_names = glob('id*')
        print 'Reading a dataset from %i Processor Files' % len(proc_names)
        N = len(proc_names)
        for i in range(N):
            if i == 0:
                fn = 'id%i/'%i + basename + '.%04i'%ddn + '.vtk'
            else:
                fn = 'id%i/'%i + basename + '-id%i'%i + '.%04i'%ddn + '.vtk'
            f = open(fn,'rb')
            print 'Reading data from %s' % fn
            line = f.readline()
            while line is not '':
                # print line
                if len(line) == 0: break
                splitup = line.strip().split()

                if "DIMENSIONS" in splitup:
                    grid_dims = na.array(splitup[-3:]).astype('int')
                    line = f.readline()
                    continue
                elif "CELL_DATA" in splitup:
                    grid_ncells = int(splitup[-1])
                    line = f.readline()
                    if na.prod(grid_dims) != grid_ncells:
                        grid_dims -= 1
                    if na.prod(grid_dims) != grid_ncells:
                        print 'product of dimensions %i not equal to number of cells %i' % \
                              (na.prod(grid_dims), grid_ncells)
                        raise TypeError
                    break
                else:
                    line = f.readline()
            read_table = False
            while line is not '':
                if len(line) == 0: break
                splitup = line.strip().split()
                if 'SCALARS' in splitup:
                    field = splitup[1]
                    if not read_table:
                        line = f.readline() # Read the lookup table line
                        read_table = True
                    data = na.fromfile(f, dtype='>f4', count=grid_ncells).reshape(grid_dims,order='F')
                    if i == 0:
                        self.fields.append(field)
                    # print 'writing field %s' % field
                    self.write_gdf_field(gdf_name, i, field, data)
                    read_table=False

                elif 'VECTORS' in splitup:
                    field = splitup[1]
                    data = na.fromfile(f, dtype='>f4', count=3*grid_ncells)
                    data_x = data[0::3].reshape(grid_dims,order='F')
                    data_y = data[1::3].reshape(grid_dims,order='F')
                    data_z = data[2::3].reshape(grid_dims,order='F')
                    if i == 0:
                        self.fields.append(field+'_x')
                        self.fields.append(field+'_y')
                        self.fields.append(field+'_z')

                    # print 'writing field %s' % field
                    self.write_gdf_field(gdf_name, i, field+'_x', data_x)
                    self.write_gdf_field(gdf_name, i, field+'_y', data_y)
                    self.write_gdf_field(gdf_name, i, field+'_z', data_z)
                    del data, data_x, data_y, data_z
                line = f.readline()
        f.close()

        f = h5.File(gdf_name,'a')
        field_g = f['field_types']
        # Add Field Attributes
        for name in self.fields:
            tname = name
            try:
                tname = translation_dict[name]
            except:
                pass
            this_field = field_g.create_group(tname)
	    if name in self.field_conversions.keys():
		this_field.attrs['field_to_cgs'] = self.field_conversions[name]
	    else:
		this_field.attrs['field_to_cgs'] = na.float64('1.0') # For Now
        f.close()

    def convert(self):
        self.read_and_write_hierarchy(self.basename, self.ddn ,self.outname)
        self.read_and_write_data(self.basename, self.ddn ,self.outname)


class AthenaConverter(Converter):
    def __init__(self, basename, outname=None, field_conversions=None):
        self.fields = []
        self.basename = basename
        name = basename.split('.')
        fn = '%s.%04i'%(name[0],int(name[1]))
        self.ddn = int(name[1])
        self.basename = fn
        if outname is None:
            outname = fn+'.gdf'
        self.outname = outname
	if field_conversions is None:
	    field_conversions = {}
	self.field_conversions = field_conversions


    def parse_line(self, line, grid):
    #    print line
        # grid is a dictionary
        splitup = line.strip().split()
        if "vtk" in splitup:
            grid['vtk_version'] = splitup[-1]
        elif "Really" in splitup:
            grid['time'] = splitup[-1]
        elif "DIMENSIONS" in splitup:
            grid['dimensions'] = na.array(splitup[-3:]).astype('int')
        elif "ORIGIN" in splitup:
            grid['left_edge'] = na.array(splitup[-3:]).astype('float64')
        elif "SPACING" in splitup:
            grid['dds'] = na.array(splitup[-3:]).astype('float64')
        elif "CELL_DATA" in splitup:
            grid["ncells"] = int(splitup[-1])
        elif "SCALARS" in splitup:
            field = splitup[1]
            grid['read_field'] = field
            grid['read_type'] = 'scalar'
        elif "VECTORS" in splitup:
            field = splitup[1]
            grid['read_field'] = field
            grid['read_type'] = 'vector'
        
    def read_grid(self, filename):
        """ Read Athena legacy vtk file from single cpu """
        f = open(filename,'rb')
        print 'Reading from %s'%filename
        grid = {}
        grid['read_field'] = None
        grid['read_type'] = None
        table_read=False
        line = f.readline()
        while line is not '':
            while grid['read_field'] is None:
                self.parse_line(line, grid)
                if grid['read_type'] is 'vector':
                    break
                if table_read is False:             
                    line = f.readline()
                if 'TABLE' in line.strip().split():
                    table_read = True
                if len(line) == 0: break
            #    print line

            if len(line) == 0: break
            if na.prod(grid['dimensions']) != grid['ncells']:
                grid['dimensions'] -= 1
            if na.prod(grid['dimensions']) != grid['ncells']:
                print 'product of dimensions %i not equal to number of cells %i' % \
                      (na.prod(grid['dimensions']), grid['ncells'])
                raise TypeError

            if grid['read_type'] is 'scalar':
                grid[grid['read_field']] = \
                    na.fromfile(f, dtype='>f4', count=grid['ncells']).reshape(grid['dimensions'],order='F')
                self.fields.append(grid['read_field'])
            elif grid['read_type'] is 'vector':
                data = na.fromfile(f, dtype='>f4', count=3*grid['ncells'])
                grid[grid['read_field']+'_x'] = data[0::3].reshape(grid['dimensions'],order='F')
                grid[grid['read_field']+'_y'] = data[1::3].reshape(grid['dimensions'],order='F')
                grid[grid['read_field']+'_z'] = data[2::3].reshape(grid['dimensions'],order='F')
                self.fields.append(grid['read_field']+'_x')
                self.fields.append(grid['read_field']+'_y')
                self.fields.append(grid['read_field']+'_z')
            else:
                raise TypeError
            grid['read_field'] = None
            grid['read_type'] = None
            line = f.readline()
            if len(line) == 0: break
        grid['right_edge'] = grid['left_edge']+grid['dds']*(grid['dimensions'])
        return grid

    def write_to_gdf(self, fn, grid):
        f = h5.File(fn,'a')

        ## --------- Begin level nodes --------- ##
        g = f.create_group('gridded_data_format')
        g.attrs['format_version']=na.float32(1.0)
        g.attrs['data_software']='athena'
        data_g = f.create_group('data')
        field_g = f.create_group('field_types')
        part_g = f.create_group('particle_types')
        pars_g = f.create_group('simulation_parameters')

        dle = grid['left_edge'] # True only in this case of one grid for the domain
        gles = na.array([grid['left_edge']])
        gdims = na.array([grid['dimensions']])
        glis = ((gles - dle)/grid['dds']).astype('int64')
        gris = glis + gdims

        # grid_left_index
        gli = f.create_dataset('grid_left_index',data=glis)
        # grid_dimensions
        gdim = f.create_dataset('grid_dimensions',data=gdims)

        levels = na.array([0]).astype('int64') # unigrid example
        # grid_level
        level = f.create_dataset('grid_level',data=levels)

        ## ----------QUESTIONABLE NEXT LINE--------- ##
        # This data needs two dimensions for now. 
        n_particles = na.array([[0]]).astype('int64')
        #grid_particle_count
        part_count = f.create_dataset('grid_particle_count',data=n_particles)

        # Assume -1 means no parent.
        parent_ids = na.array([-1]).astype('int64')
        # grid_parent_id
        pids = f.create_dataset('grid_parent_id',data=parent_ids)

        ## --------- Done with top level nodes --------- ##

        f.create_group('hierarchy')

        ## --------- Store Grid Data --------- ##

        g0 = data_g.create_group('grid_%010i'%0)
        for field in self.fields:
            name = field
            if field in translation_dict.keys():
                name = translation_dict[name]
            if not name in g0.keys(): 
                g0.create_dataset(name,data=grid[field])

        ## --------- Store Particle Data --------- ##

        # Nothing to do

        ## --------- Attribute Tables --------- ##

        pars_g.attrs['refine_by'] = na.int64(1)
        pars_g.attrs['dimensionality'] = na.int64(3)
        pars_g.attrs['domain_dimensions'] = grid['dimensions']
        try:
            pars_g.attrs['current_time'] = grid['time']
        except:
            pars_g.attrs['current_time'] = 0.0
        pars_g.attrs['domain_left_edge'] = grid['left_edge'] # For Now
        pars_g.attrs['domain_right_edge'] = grid['right_edge'] # For Now
        pars_g.attrs['unique_identifier'] = 'athenatest'
        pars_g.attrs['cosmological_simulation'] = na.int64(0)
        pars_g.attrs['num_ghost_zones'] = na.int64(0)
        pars_g.attrs['field_ordering'] = na.int64(0)
        pars_g.attrs['boundary_conditions'] = na.int64([0]*6) # For Now

        # Extra pars:
        pars_g.attrs['n_cells'] = grid['ncells']
        pars_g.attrs['vtk_version'] = grid['vtk_version']

        # Add Field Attributes
        for name in g0.keys():
            tname = name
            try:
                tname = translation_dict[name]
            except:
                pass
            this_field = field_g.create_group(tname)
	    if name in self.field_conversions.keys():
		this_field.attrs['field_to_cgs'] = self.field_conversions[name]
	    else:
		this_field.attrs['field_to_cgs'] = na.float64('1.0') # For Now

        # Add particle types
        # Nothing to do here

        # Add particle field attributes
        f.close()

    def convert(self):
        grid = self.read_grid(self.basename+'.vtk')
        self.write_to_gdf(self.outname,grid)
        
# import sys
# if __name__ == '__main__':
#     n = sys.argv[-1]
#     n = n.split('.')
#     fn = '%s.%04i'%(n[0],int(n[1]))
#     grid = read_grid(fn+'.vtk')
#     write_to_hdf5(fn+'.gdf',grid)
    
