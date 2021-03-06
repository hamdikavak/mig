"""
ABMs in the Study of Forced Migration
Author: Melonie Richey
Last Edited: 2020

Spatially-Explicit Agent-Based Model (ABM) of Forced Migration from Syria to Turkey ###
"""
import json
import os
import shutil
import sys
import csv
import time
import math
import copy
import random
import unidecode
import numpy as np
import pandas as pd
import networkx as nx
import geopandas as gpd
import matplotlib.pyplot as plt
from functools import partial
from multiprocessing.pool import Pool

# CONSTANTS
config = {
    # For Testing - set test to True
    'test': True,
    'num_nodes': 100,
    'avg_num_neighbors': 5,
    'total_refs': 500,  # refs per node = TOTAL_NUM_REFUGEES / NUM_NODES

    # For running time trials
    'time_trial': False,

    # Data commands (not available while testing)
    'preprocess': False,

    # Validation (not available while testing)
    'validate': False,

    # Sim params
    'data_dir': './data',  # (not used while testing)
    'output_dir': './output',

    # Output params
    # Whether to visualize the geographic network
    'draw_geo_graph': False,
    # Whether to print the node weights at the end of running
    'print_node_weights': False,
    # Whether to write shapefiles every time step
    'write_step_shapefiles': True,  # (not available while testing)

    # Set number of simulation steps; 1 step = 1 day
    'num_steps': 1,

    # Number of friendships and kin to create
    'num_friends': 1,  # int for defined number. Tuple (low, high) for random number of friends
    'num_kin': 1,  # int for defined number. Tuple (low, high) for random number of friends

    # Percentage of refugees that move if in a district with one or more refugee camps
    'percent_move_at_camp': 0.3,
    # Percentage of refugees that move if in a district with one of more conflict events
    'percent_move_at_conflict': 1,
    # Percentage of refugees that move if in a district without a conflict event or a camp
    'percent_move_other': 0.7,

    # Point to calculate western movement
    'location': (51.5074, -0.1278),

    # Weight of each of the node desirability variables
    'population_weight': 0.25,  # total number of refs at node
    'location_weight': 0.25,  # closeness to LOCATION point
    'camp_weight': 0.25,  # (camps * CAMP_WEIGHT)
    'conflict_weight': 0.25,  # (conflicts * (-1) * CONFLICT_WEIGHT)
    'kin_weight': 0.25,  # (num kin * FRIEND_WEIGHT)
    'friend_weight': 0.25,  # (num friends * KIN_WEIGHT)

    # Number of refugees to seed each node in border crossing with. new refs = seed_refs * len(seed_nodes)
    'seed_refs': 0,  # If this is 0, seeding will not occur
    'seed_nodes': ['Merkez Kilis', 'KarkamA+-A', 'YayladaAA+-', 'Kumlu'],  # [0, 1, 2, 3]

    # Number of chunks (processes) to split refugees into during a sim step
    # These dont necessarily have to be equal
    'num_batches': 4,
    'num_processes': 4  # mp.cpu_count()

}

# Create directories for output
if not os.path.exists(config['output_dir']):
    os.makedirs(config['output_dir'])
if not os.path.exists(os.path.join(config['output_dir'], 'shapefiles/')):
    os.makedirs(os.path.join(config['output_dir'], 'shapefiles/'))

# Write params to file
with open(os.path.join(config['output_dir'], 'parameters.json'), 'w+') as fp:
    json.dump(config, fp, indent=4)


def find_new_node(sim, node, ref):
    # find neighbor with highest weight
    neighbors = list(sim.graph.neighbors(node))

    # initialize max node value to negative number
    most_desirable_score = -99
    most_desirable_neighbor = None

    # check to see if there are neighbors (in case node is isolate)
    if len(neighbors) == 0:
        # print(ref_pop, "refugees can't move from isolates", node)
        return

    kin_nodes = [sim.all_refugees[kin].node for kin in sim.all_refugees[ref].kin_list]
    friend_nodes = [sim.all_refugees[friend].node for friend in sim.all_refugees[ref].friend_list]

    # calculate neighbor with highest population
    for n in neighbors:
        kin_at_node = kin_nodes.count(n)
        friends_at_node = friend_nodes.count(n)
        desirability = (kin_at_node * config['kin_weight']) + (friends_at_node * config['friend_weight']) + \
                       sim.graph.nodes[n][
                           'node_score']  # + self.graph.nodes[n]['']
        if desirability > most_desirable_score:
            most_desirable_score = desirability
            most_desirable_neighbor = n

    return most_desirable_neighbor


def process_refs(refs, sim):
    new_refs = []
    ref_nodes = {key: [] for key in sim.graph.nodes}

    for x, ref in enumerate(refs):
        node = sim.all_refugees[ref].node
        num_conflicts = sim.graph.nodes[node]['num_conflicts']
        num_camps = sim.graph.nodes[node]['num_camps']

        new_weights = {key: 0 for key in sim.graph.nodes}

        if num_conflicts > 0:
            # Conflict zone
            move = True
        elif num_camps > 0:
            # At a camp
            move = random.random() < config['percent_move_at_camp']
        else:
            # Neither camp nor conflict
            move = random.random() < config['percent_move_other']

        new_refs.append(copy.deepcopy(sim.all_refugees[ref]))

        if move:
            new_node = find_new_node(sim, node, ref)
            if new_node:
                new_refs[x].node = new_node
                # Update weight dict
                # Subtract the refugee that left
                # Add the refugee that entered
                new_weights[node] -= 1
                new_weights[new_node] += 1

    # return new refugee list and node weight updates for these refs
    return new_refs, ref_nodes


class Ref(object):
    """
    Class representative of a single refugee
    """

    def __init__(self, node, num_refugees):
        self.node = node  # Not used. Agent ID == Index in Sim.all_refugees
        self.kin_list = {}
        self.friend_list = {}

    def create_defined_social_links(self, index, sim):
        # create kin
        for x in range(config['num_kin']):
            kin = index
            while kin == index:
                kin = random.randint(0, sim.num_refugees - 1)
            self.kin_list[kin] = 1
            # set for other kin
            sim.all_refugees[kin].kin_list[index] = 1

        # create friends
        for x in range(config['num_friends']):
            friend = index
            while friend == index:
                friend = random.randint(0, sim.num_refugees - 1)
            self.friend_list[friend] = 1
            # set for other friend
            sim.all_refugees[friend].friend_list[index] = 1

    def create_random_social_links(self, index, sim):
        # create kin
        for x in range(random.randint(config['num_kin'][0], config['num_kin'][1])):
            kin = index
            while kin == index:
                kin = random.randint(0, sim.num_refugees - 1)
            self.kin_list[kin] = 1
            # set for other kin
            sim.all_refugees[kin].kin_list[index] = 1

        # create friends
        for x in range(random.randint(config['num_friends'][0], config['num_friends'][1])):
            friend = index
            while friend == index:
                friend = random.randint(0, sim.num_refugees - 1)
            self.friend_list[friend] = 1
            # set for other friend
            sim.all_refugees[friend].friend_list[index] = 1


class Sim(object):
    """
    Class representative of the simulation
    """

    def __init__(self, graph, num_steps=10, num_processes=1, num_batches=1):
        self.graph = graph
        self.num_steps = num_steps
        self.num_processes = num_processes
        self.num_batches = num_batches
        self.num_refugees = sum([self.graph.nodes[n]['weight'] for n in self.graph.nodes])
        self.all_refugees = []
        for node in self.graph.nodes():
            self.all_refugees.extend([Ref(node, self.num_refugees) for x in range(self.graph.nodes[node]['weight'])])

        if isinstance(config['num_friends'], int):
            for index, ref in enumerate(self.all_refugees):
                ref.create_defined_social_links(index, self)
        else:
            for index, ref in enumerate(self.all_refugees):
                ref.create_random_social_links(index, self)

    def step(self):
        nx.get_node_attributes(self.graph, 'weight')
        orig_weights = nx.get_node_attributes(self.graph, 'weight')

        # Update normalized node weights
        max_pop = max(orig_weights.values())
        norm_weights = [x / max_pop for x in orig_weights.values()]
        norm_weights = dict(zip(orig_weights.keys(), norm_weights))
        nx.set_node_attributes(self.graph, norm_weights, 'norm_weight')

        # Normalize camps
        num_camps = nx.get_node_attributes(self.graph, 'num_camps')
        max_camps = max(list(num_camps.values()) + [1])
        num_camps = [x / max_camps for x in num_camps.values()]

        # Normalize conflicts
        num_conflicts = nx.get_node_attributes(self.graph, 'num_conflicts')
        max_conflict = max(list(num_conflicts.values()) + [1])  # Add a max of 1 to prevent division by zero error
        num_conflicts = [x / max_conflict for x in num_conflicts.values()]

        # Update node score
        location_scores = nx.get_node_attributes(self.graph, 'location_score')
        node_scores = [
            (w * config['population_weight']) + (x * config['location_weight']) + (y * config['camp_weight']) + (
                        z * (-1) * config['conflict_weight']) for
            w, x, y, z in
            zip(norm_weights.values(), location_scores.values(), num_camps, num_conflicts)]
        node_scores = dict(zip(norm_weights.keys(), node_scores))
        nx.set_node_attributes(self.graph, node_scores, 'node_score')

        # Whether to process in parallel or synchronously
        if self.num_processes > 1:
            print(f'Staring {self.num_processes} processes...')
            # Chunk refs and send to processes
            chunked_refs = np.array_split([x for x in range(len(self.all_refugees))], self.num_batches)
            partial_pr = partial(process_refs, sim=self)
            pool = Pool(self.num_processes)

            results = pool.map(partial_pr, chunked_refs)
            pool.close()
            pool.join()
        else:
            print('Not Multiprocessing')
            results = [process_refs([x for x in range(len(self.all_refugees))], self)]

        self.all_refugees = []
        new_weights = [orig_weights]
        for result in results:
            self.all_refugees.extend(result[0])
            new_weights.append(result[1])

        new_weights = pd.DataFrame(new_weights)
        new_weights = dict(zip(self.graph.nodes, list(new_weights.sum(numeric_only=True))))
        nx.set_node_attributes(self.graph, new_weights, 'weight')

        if config['seed_refs'] > 0:
            print('Seeding network at border crossings...')
            new_ref_index = self.num_refugees
            self.num_refugees += config['seed_refs'] * len(config['seed_nodes'])
            for node in config['seed_nodes']:
                self.graph.nodes[node]['weight'] += config['seed_refs']
                self.all_refugees.extend([Ref(node, self.num_refugees) for x in range(0, config['seed_refs'])])

            for index in range(new_ref_index, self.num_refugees):
                self.all_refugees[index].create_social_links(index, self)

    def run(self):
        avg_step_time = 0
        for x in list(range(self.num_steps)):
            start = time.time()
            print(f'Starting step {x + 1}...')
            self.step()

            if config['write_step_shapefiles'] and not config['test']:
                node_weights = nx.get_node_attributes(self.graph, 'weight')
                # Write out to shapefile
                polys['REFPOP'] = polys['NAME_2'].map(node_weights)
                polys.to_file(os.path.join(config['output_dir'], 'shapefiles/', f'simOutput_{x:03}.shp'))

            step_time = time.time() - start
            avg_step_time += step_time
            print(f'Step Time: {step_time:2f}')

        avg_step_time /= self.num_steps
        print(f'Average step time: {avg_step_time:2f}')

        return avg_step_time


def preprocess():
    # ***Data Engineering Using GeoPandas***

    # Get shapefile of Turkish districts (and re-project)
    polys = gpd.read_file(os.path.join(config['data_dir'], 'gadm36_TUR_2.shp'))

    # Check and set spatial projection
    # polys = polys.to_crs({'init': 'epsg:4326'})
    # print(polys.crs)

    # Get refugee population by province data (and re-project), from Turkish Statistical Institute, February 2019
    pop_by_province = gpd.read_file(os.path.join(config['data_dir'], 'REFPOP.shp'))

    # Check and set spatial projection
    # pop_by_province = pop_by_province.to_crs({'init': 'epsg:4326'})
    # print(pop_by_province.crs)

    # ** Remove non-english characters **
    # ** fix duplicate district names ("Merkez") **
    new_names = []  # for tracking nodes with same name. We will append index in dataframe if not unique
    for index, row in polys.iterrows():
        name = unidecode.unidecode(row.NAME_2)
        if name in "Merkez":
            name += " " + unidecode.unidecode(row.NAME_1)
        if name in new_names:
            name += str(index)
        new_names.append(name)
    polys["NAME_2"] = new_names

    # ** Add Population Data **
    polys["REFPOP"] = 0
    # Assign an equal portion of refs to each node in province
    for index, row in pop_by_province.iterrows():
        # print(row['REFPOP'], row['count'])
        refs_at_node = row['REFPOP'] / row['count']
        if math.isnan(refs_at_node):
            refs_at_node = 0
        polys.loc[polys.NAME_1 == row.NAME_1, 'REFPOP'] = int(refs_at_node)

    # ** Add conflict data **
    # Read in ACLED event data from February 2019 (and set projection)
    df_conflict = pd.read_csv(os.path.join(config['data_dir'], 'FebACLEDextract.csv'))
    conflict = gpd.GeoDataFrame(df_conflict,
                                geometry=gpd.points_from_xy(df_conflict.longitude, df_conflict.latitude),
                                crs='epsg:4326')
    # conflict.crs = 'epsg:4326'
    # Create new column in target shapefile for count of conflict events per district
    polys["conflict"] = polys.apply(lambda row: sum(conflict.within(row.geometry)), axis=1)

    # ** Add camps **
    # Read in refugee camp data (and re-project) from UNHCR Regional IM Working Group February 2019 (updated every 6 months)
    camps = gpd.read_file(os.path.join(config['data_dir'], 'tur_camps.shp'))
    # Create new column in target shapefile for refugee camps
    polys["camp"] = polys.apply(lambda row: sum(camps.within(row.geometry)), axis=1)

    ## Add location score
    # Calculate location score. Districts closest to specified location are scored highest.
    polys['location'] = polys.apply(
        lambda row: math.sqrt(
            (row.geometry.centroid.x - config['location'][1]) ** 2 + (row.geometry.centroid.y - config['location'][0]) ** 2), axis=1)
    max_distance = max(list(polys['location']))
    polys['location'] = polys.apply(lambda row: 1 - (row.location / max_distance), axis=1)

    ## Create centroids GPD
    points = polys.copy()
    points['geometry'] = points['geometry'].centroid

    # Write points to new Shapefile
    points.to_file(os.path.join(config['data_dir'], 'preprocessed_data.shp'))

    # Write polys to new Shapefile
    polys.to_file(os.path.join(config['data_dir'], 'preprocessed_poly_data.shp'))

    return polys, points, pop_by_province


def build_graph(data):
    # ***Network Creation using NetworkX***
    graph = nx.Graph()

    if isinstance(data, str):
        data = gpd.read_file(data)

    # ***Add Nodes to Graph***
    positions = {}
    for index, row in data.iterrows():
        node = row.NAME_2
        # Add the coordinates to the nodes so they can be displayed geospatially
        coords = row['geometry'].centroid
        graph.add_node(node, pos=coords, weight=row.REFPOP,
                       num_conflicts=row.conflict, num_camps=row.camp,
                       location_score=row.location)

        positions[node] = (coords.x, coords.y)

        nx.set_node_attributes(graph, positions, 'position')

        # ***Add Edges to Graph***
        neighbors = data[data.geometry.touches(row['geometry'])].NAME_2.tolist()
        for n in neighbors:
            edge = (row.NAME_2, n)
            edge = sorted(edge)
            graph.add_edge(edge[0], edge[1], weight=1)

    return graph


def draw(polys, graph):
    polys.plot(color='cadetblue', edgecolor='black')
    nx.draw(graph, node_size=25, node_color='darkblue', pos=nx.get_node_attributes(graph, 'position'))
    plt.show()


def time_trial(graph, output_file='results.csv', num_steps=10, num_processes=[1], num_batches=[1]):
    with open(output_file, 'w+', newline='') as fp:
        writer = csv.writer(fp)
        writer.writerow(['STEPS', 'PROCESSES', 'BATCHES', 'TIME_(S)'])
        for n_process in num_processes:
            #             for n_batch in num_batches:
            sim = Sim(graph, num_steps, n_process, n_process)
            avg_step_time = sim.run()

            writer.writerow([num_steps, n_process, n_process, avg_step_time])
            fp.flush()


if __name__ == '__main__':
    """
    Program Execution starts here
    """

    if config['test']:
        print('Building test graph...')
        # For testing
        graph = nx.fast_gnp_random_graph(config['num_nodes'], float(config['avg_num_neighbors']) / config['num_nodes'])
        num_breaks = 50
        refs = int(float(config['total_refs']) / num_breaks)
        refs_per_node = {key: 0 for key in graph.nodes()}
        for x in range(0, num_breaks):
            node = random.choice(list(graph.nodes()))
            refs_per_node[node] += refs

        nx.set_node_attributes(graph, name='weight', values=refs_per_node)
        nx.set_node_attributes(graph, name='num_conflicts', values=1)  # todo - can make this random
        nx.set_node_attributes(graph, name='num_camps', values=0)  # todo - can make this random
        nx.set_node_attributes(graph, name='location_score', values=0.5)  # todo - can make this random
    else:
        if config['preprocess']:  # Run pre-processing
            # Option 1 - Preprocess shapefiles
            print('Pre-processing graph data...')
            start = time.time()
            polys, points, pop_by_province = preprocess()
            print(f'Completed in {time.time() - start:.2f}s...')
        else:
            # Option 2 - Build graph from preprocessed polys shapefile
            print('Loading graph data from file...')
            start = time.time()
            polys = gpd.read_file('../data/preprocessed_poly_data.shp')
            print(f'Completed in {time.time() - start:.2f}s...')

        graph = build_graph(polys)

        # Draw graph
        if config['draw_geo_graph']:
            print('Drawing graph...')
            draw(polys, graph)

    if config['time_trial']:
        num_steps = 5
        processes = [1, 2, 4, 8, 16]  # , 4, 8, 12, 16]
        chunks = [1]  # , 4, 8, 12, 16]

        time_trial(graph, num_steps=num_steps, num_processes=processes, num_batches=chunks)
        sys.exit()

    # Run Sim
    print('Creating sim...')
    start = time.time()
    sim = Sim(graph, config['num_steps'], config['num_processes'], config['num_batches'])
    print(f'Created sim in {time.time() - start:.2f}s...')

    start_node_weights = nx.get_node_attributes(sim.graph, 'weight')
    sim.run()
    end_node_weights = nx.get_node_attributes(sim.graph, 'weight')
    if config['print_node_weights']:
        for node in graph.nodes:
            print(node, start_node_weights[node], end_node_weights[node])

    print("Total start weight:", sum(start_node_weights.values()))
    print("Total end weight:", sum(end_node_weights.values()))

    if not config['test']:
        # Write out to shapefile
        polys['simEnd'] = polys['NAME_2'].map(end_node_weights)
        polys.to_file(os.path.join(config['data_dir'], 'simOutput.shp'))

    if config['validate'] and not config['test']:
        ## MODEL VALIDATION ##
        val = gpd.read_file(os.path.join(config['data_dir'], 'gadm36_TUR_1_val.shp'))
        pop_by_province = gpd.read_file(os.path.join(config['data_dir'], 'REFPOP.shp'))
        # sim_val = gpd.read_file(r"C:\Users\mrich\OneDrive\GMU\Summer 2019 Comp Migration\output_3_simOutput.shp")

        # Remove non-english characters and fix duplicate district names ("Merkez")
        for index, row in val.iterrows():
            name = unidecode.unidecode(row.NAME_1)
            val.at[index, "NAME_1"] = name

        # Take refugee population by province, divide by number of districts in province, assign each equivalent value as REFPOP of district
        polys['valPop'] = 0
        for index, row in val.iterrows():
            # print(type(row['val_mar19']))
            # print(type(polys_val.loc[index].count))

            val_calc = row['val_mar19'] / float(pop_by_province.loc[index]['count'])
            # print(polys.NAME_1)
            if not math.isnan(val_calc):
                val_calc = int(val_calc)

                polys.valPop.iloc[[polys.NAME_1 == row.NAME_1]] = val_calc

        # Normalize both actual and predicted REFPOP for district-level comparison
        minPop = min(polys.valPop)
        maxPop = max(polys.valPop)
        polys['valPopNorm'] = (polys['valPop'] - minPop) / (maxPop - minPop)
        minPop = min(polys.simEnd)
        maxPop = max(polys.simEnd)
        polys['simEnd_norm'] = (polys['simEnd'] - minPop) / (maxPop - minPop)
        # print(polys.simEnd_norm)

        # Comparative scaled_actual & scale_predicted
        polys['accuracy'] = (polys.simEnd_norm - polys.valPopNorm)

        # Write out new shapefile with validation accuracy by district
        polys.to_file(os.path.join(config['data_dir'], 'validationResults.shp'))

        # visualize validation output - unnecessary to read again
        # validation = gpd.read_file(os.path.join(DATA_DIR, 'output_5_validation.shp'))
        colors = 6
        figsize = (26, 20)
        cmap = 'winter_r'
        accuracy = polys.accuracy
        polys.plot(column=accuracy, cmap=cmap, scheme='equal_interval', k=colors, legend=True, linewidth=10)

        # SHOW PLOTS
        plt.show()
