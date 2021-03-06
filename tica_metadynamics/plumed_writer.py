#!/bin/env python
from jinja2 import Template
from msmbuilder.utils import load
import numpy as np

plumed_dist_template = Template("DISTANCE ATOMS={{atoms}} LABEL={{label}} ")
plumed_torsion_template = Template("TORSION ATOMS={{atoms}} LABEL={{label}} ")
plumed_angle_template = Template("ANGLE ATOMS={{atoms}} LABEL={{label}} ")
plumed_rmsd_template = Template("RMSD REFERENCE={{loc}} TYPE=OPTIMAL LABEL={{label}} ")
plumed_min_dist_template = Template("DISTANCES GROUPA={{group_a}} GROUPB={{group_b}} MIN={BETA={{beta}}} LABEL={{label}}")

plumed_matheval_template = Template("MATHEVAL ARG={{arg}} FUNC={{func}} LABEL={{label}} PERIODIC={{periodic}} ")

plumed_combine_template = Template("COMBINE LABEL={{label}} ARG={{arg}} COEFFICIENTS={{coefficients}} "+\
                                    "PERIODIC={{periodic}} ")

plumed_plain_metad_template = Template("METAD ARG={{arg}} SIGMA={{sigma}} HEIGHT={{height}} "+\
                                       "FILE={{hills}} TEMP={{temp}} PACE={{pace}} LABEL={{label}}")

base_metad_script="METAD ARG={{arg}} SIGMA={{sigma}} HEIGHT={{height}} "+\
                    "FILE={{hills}} TEMP={{temp}} PACE={{pace}} LABEL={{label}}"

bias_factor_format = "BIASFACTOR={{biasfactor}}"

interval_format = "INTERVAL={{interval}}"

grid_format = "GRID_MIN={{GRID_MIN}} GRID_MAX={{GRID_MAX}}"

plumed_wall_template = Template("{{wall_type}}_WALLS ARG={{arg}} AT={{at}} "
                         "KAPPA={{kappa}} EXP={{exp}} EPS={{eps}} OFFSET={{offset}} LABEL={{label}}")

plumed_print_template = Template("PRINT ARG={{arg}} STRIDE={{stride}} FILE={{file}} ")

_SUPPORTED_FEATS=["Contact","LandMarkFeaturizer","Dihedral","AlphaAngle", "Kappa"]


def create_torsion_label(inds, label):
    #t: TORSION ATOMS=inds
    return plumed_torsion_template.render(atoms=','.join(map(str, inds)), label=label) +"\n"

def create_angle_label(inds, label):
    #t: ANGLE ATOMS=inds
    return plumed_angle_template.render(atoms=','.join(map(str, inds)), label=label) +"\n"

def create_distance_label(inds, label):
    return plumed_dist_template.render(atoms=','.join(map(str, inds)), label=label) + "\n"

def create_min_dist_label(group_a,group_b,beta,label):
    return plumed_min_dist_template.render(group_a=','.join(map(str, group_a)),
                                           group_b=','.join(map(str, group_b)),
                                           beta=beta,
                                           label=label)+ "\n"

def create_rmsd_label(loc, label):
    return plumed_rmsd_template.render(loc=loc , label=label) + "\n"


def create_mean_free_label(feature_label, offset, func=None,
                    feature_mean=None, feature_scale=None, **kwargs):
    arg = feature_label
    # if feature_scale is not None and feature_mean is not None:
    #     x = "((x-%s)/%s)"%(feature_mean, feature_scale)
    # else:
    #     x="x"
    x="x"
    if func is None:
        if feature_scale is not None and feature_mean is not None:
            f ="(%s-%s)/%s-%s"%(x,feature_mean, feature_scale, offset)
        else:
            f ="%s-%s"%(x, offset)
        label= "meanfree_"+ "%s_"%func + feature_label

    elif func=="min":
        if feature_scale is not None and feature_mean is not None:
            f ="(%s-%s)/%s-%s"%(x,feature_mean, feature_scale, offset)
        else:
            f ="%s-%s"%(x, offset)
        label= "meanfree_"+ "%s_"%func + feature_label.strip(".min")

    elif func=="exp":
        if feature_scale is not None and feature_mean is not None:
            f ="(%s(-(%s)^2/(2*%s^2)))-%s)/%s-%s"%(func, x,kwargs.pop("sigma"),
                                                   feature_mean, feature_scale, offset)
        else:
            f = "%s(-(%s)^2/(2*%s^2))-%s"%(func, x, kwargs.pop("sigma"), offset)
        label= "meanfree_"+ "%s_"%func + feature_label

    elif func in ["sin","cos"]:
        if feature_scale is not None and feature_mean is not None:
            f = "(%s(%s)-%s)/%s-%s"%(func,x,feature_mean, feature_scale, offset)
        else:
            f = "%s(%s)-%s"%(func,x,offset)
        label= "meanfree_"+ "%s_"%func + feature_label

    else:
        raise ValueError("Can't find function")


    return plumed_matheval_template.render(arg=arg, func=f,\
                                           label=label,periodic="NO")


class PlumedWriter(object):
    """
    protein class to write all tica metadynamics files
    """
    def __init__(self, yaml_file, grid=True, interval=True, interval_lim=(0.01,0.99),\
                 pace=1000, biasfactor=20, temp=300):
        self.yaml_file = load_yaml_file(yaml_file)
        self.prj = load(yaml_file["prj_file"])
        self.tica_mdl = prj.tica_mdl
        self.df = prj.df
        self.grid = grid
        if self.grid:
            self.grid_min,self.grid_max = get_interval(self.prj,0,100)
        self.interval = interval
        self.interval_lim = interval_lim
        if self.interval:
            self.interval_min,self.interval_max = get_interval(self.prj,interval_lim[0],interval_lim[1])

        self.pace = pace
        self.biasfactor = biasfactor
        self.temp = temp


    def render_plumed(self):
        return write_plumed_file(self.tica_mdl, self.df)

def get_interval(tica_data,lower,upper):
    if type(tica_data)==dict:
        res = np.percentile(np.concatenate([tica_data[i] for \
                                             i in tica_data.keys()]),(lower, upper), axis=0)
    else:
        res = np.percentile(np.concatenate([i for i in tica_data]),(lower, upper), axis=0)
    return [i for i in zip(res[0],res[1])]

def get_feature_function(df, feature_index):
    possibles = globals().copy()
    possibles.update(locals())
    if df.featurizer[feature_index] == "Contact" and len(df.atominds[feature_index][0])==1:
        func = possibles.get("create_distance_label")
    elif df.featurizer[feature_index] == "Contact" and len(df.atominds[feature_index][0])>1:
        func = possibles.get("create_min_dist_label")
    elif df.featurizer[feature_index] == "LandMarkFeaturizer":
        func = possibles.get("create_rmsd_label")
    elif df.featurizer[feature_index] == "Kappa":
        func = possibles.get("create_angle_label")
    else:
        func = possibles.get("create_torsion_label")
    return func

def render_raw_features(df,inds):
    output = []
    if not set(df.featurizer).issubset(set(_SUPPORTED_FEATS)):
        raise ValueError("Sorry only contact, landmark, and dihedral featuizers\
                         are supported for now")
    possibles = globals().copy()
    possibles.update(locals())

    already_done_list = []

    for j in df.iloc[inds].iterrows():
        feature_index = j[0]
        atominds = np.array(j[1]["atominds"])
        resids = j[1]["resids"]
        feat = j[1]["featuregroup"]
        func = get_feature_function(df, feature_index)
        if  df.featurizer[feature_index] == "LandMarkFeaturizer":
            feat_label =  feat+"_%s"%feature_index
        else:
            feat_label = feat+"_%s"%'_'.join(map(str,resids))
        if feat_label not in already_done_list:
            #mdtraj is 0 indexed and plumed is 1 indexed
            if  df.featurizer[feature_index] == "LandMarkFeaturizer":
                output.append(func("../pdbs/%d.pdb"%feature_index , feat_label))
            elif  df.featurizer[feature_index] == "Contact" and len(df.atominds[feature_index][0])==1:
                output.append(func(inds=[np.array(atominds[0][0])+1,
                                         np.array(atominds[1][0])+1],
                                   label=feat_label))
            elif  df.featurizer[feature_index] == "Contact" and len(df.atominds[feature_index][0])>1:
                output.append(func(group_a=np.array(atominds[0])+1,
                                   group_b=np.array(atominds[1])+1,
                                   beta=df.otherinfo[feature_index] ,
                                   label=feat_label))
            else:
                output.append(func(atominds + 1 , feat_label))
            output.append("\n")
            already_done_list.append(feat_label)

    return ''.join(output)

def match_mean_free_function(df, feature_index):
    possibles = globals().copy()
    possibles.update(locals())
    if df.featurizer[feature_index] == "Contact" and len(df.atominds[feature_index][0])==1:
        func = None
    elif df.featurizer[feature_index] == "Contact" and len(df.atominds[feature_index][0])>1:
        func = "min"
    elif df.featurizer[feature_index] == "LandMarkFeaturizer":
        func = "exp"
        sigma =  df.otherinfo[feature_index]
    else:
        func = df.otherinfo[feature_index]
    return func

def render_mean_free_features(df, inds, tica_mdl, nrm=None):
    output = []
    if not set(df.featurizer).issubset(set(_SUPPORTED_FEATS)):
        raise ValueError("Sorry only contact, landmark, and dihedral featuizers\
                         are supported for now")


    for j in df.iloc[inds].iterrows():
        feature_index = j[0]
        atominds = j[1]["atominds"]
        feat = j[1]["featuregroup"]
        resids = j[1]["resids"]
        feat = j[1]["featuregroup"]
        func = match_mean_free_function(df, feature_index,)
        if  df.featurizer[feature_index] == "LandMarkFeaturizer":
            feat_label =  feat+"_%s"%feature_index
        elif df.featurizer[feature_index] == "Contact" and len(df.atominds[feature_index][0])>1:
            feat_label = feat+"_%s"%'_'.join(map(str,resids))+".min"
        else:
            feat_label = feat+"_%s"%'_'.join(map(str,resids))
        sigma = None
        if nrm is not None:
            if hasattr(nrm, "center_"):
                nrm.mean_ = nrm.center_
            output.append(create_mean_free_label(feature_label=feat_label,\
                                             offset=tica_mdl.means_[feature_index],\
                                             func =func, \
                                             feature_mean = nrm.mean_[feature_index],
                                             feature_scale = nrm.scale_[feature_index],
                                             sigma=sigma)+"\n")

        else:
            output.append(create_mean_free_label(feature_label=feat_label,\
                                             offset=tica_mdl.means_[feature_index],\
                                             func =func, sigma=sigma)+"\n")
        output.append("\n")

    return ''.join(output)

def render_tic(df,tica_mdl, tic_index=0):
    output = []
    inds = np.nonzero(tica_mdl.components_[tic_index,:])[0]
    template = Template("meanfree_{{func}}_{{feature_group}}_{{feature_index}}")

    func = np.array([match_mean_free_function(df, i) for i in inds])
    if  df.featurizer[0] == "LandMarkFeaturizer":
        feat_labels =  [i for i in range(len(df))]
    else:
        feat_labels = ['_'.join(map(str,i)) for i in df.resids[inds]]
    feature_labels = [template.render(func=i,feature_group=j,feature_index=k) \
                      for i,j,k in zip(func,df.featuregroup[inds],feat_labels)]

    tic_coefficient = tica_mdl.components_[tic_index,inds]
    if tica_mdl.kinetic_mapping:
        raise ValueError("Sorry but kinetic mapping or is not supported for now")
        #tic_coefficient *= tica_mdl.eigenvalues_[tic_index]

    arg=','.join(feature_labels)
    tic_coefficient = ','.join(map(str,tic_coefficient))

    output.append(plumed_combine_template.render(arg=arg,
                                   coefficients=tic_coefficient,
                                   label="tic%d"%tic_index,
                                   periodic="NO") +"\n")
    return ''.join(output)


def render_metad_code(arg="tic0", sigma=0.2, height=1.0, hills="HILLS",biasfactor=40,
                      temp=300,interval=None, grid=None,
                      label="metad",pace=1000, walker_n = None, walker_id=None,
                      **kwargs):

    output=[]
    base_metad_script="METAD ARG={{arg}} SIGMA={{sigma}} HEIGHT={{height}} "+\
                    "FILE={{hills}} TEMP={{temp}} PACE={{pace}} LABEL={{label}}"
    bias_factor_format = "BIASFACTOR={{biasfactor}}"
    interval_format = "INTERVAL={{interval}}"
    grid_format = "GRID_MIN={{grid_min}} GRID_MAX={{grid_max}}"
    walker_format="WALKERS_N={{walker_n}} WALKERS_ID={{walker_id}} "+\
                   "WALKERS_DIR={{walker_dir}} WALKERS_RSTRIDE={{walker_stride}}"
    if biasfactor is not None:
        base_metad_script = ' '.join((base_metad_script, bias_factor_format))
    if interval is not None:
        base_metad_script = ' '.join((base_metad_script, interval_format))
    if grid is not None:
        base_metad_script = ' '.join((base_metad_script, grid_format))
    if walker_id is not None:
        base_metad_script = ' '.join((base_metad_script,walker_format))
        walker_stride = pace * 10
        if ',' in arg:
          walker_dir = "../../data_tic0"
        else:
          walker_dir = "../../data_%s"%arg
    else:
        walker_stride=walker_dir=None
    plumed_metad_template = Template(base_metad_script)

    plumed_script = plumed_metad_template

    if grid is None:
        grid_min=grid_max=0
    else:
        grid_min = grid[0]
        grid_max = grid[1]
    if interval is None:
        interval=[0]

    output.append(plumed_script.render(arg=arg,
                         sigma=sigma,
                         height=height,
                         hills=hills,
                         biasfactor=biasfactor,
                         interval=','.join(map(str,interval)),
                         grid_min=grid_min,
                         grid_max=grid_max,
                         label=label,
                         pace=pace,
                         temp=temp,
                         walker_id=walker_id,
                         walker_n=walker_n,
                         walker_stride=walker_stride,
                         walker_dir=walker_dir) +"\n")
    return ''.join(output)


def render_metad_bias_print(arg="tic0",stride=1000,label="metad",file="BIAS"):
    """
    :param arg: tic name
    :param stride: stride for printing
    :param label: label for printing
    :param file:
    :return:
    """
    output=[]
    arg=','.join([arg,label + ".bias"])
    output.append(plumed_print_template.render(arg=arg,
                                               stride=stride,
                                               file=file))

    return ''.join(output)

def render_tic_wall(arg,wall_limts,**kwargs):
    """
    :param arg: tic name
    :param stride: stride for printing
    :param label: label for printing
    :param file:
    :return:
    """
    output=[]
    for i,wall_type in enumerate(["LOWER","UPPER"]):
        output.append(plumed_wall_template.render(wall_type=wall_type,
                                                  arg=arg,
                                                  at=wall_limts[i],
                                                  kappa=150,
                                                  exp=2,
                                                  eps=1,
                                                  offset=0,
                                                  label=wall_type.lower()))
        output.append("\n")
    return ''.join(output)

def render_tica_plumed_file(tica_mdl, df, n_tics, grid_list=None,interval_list=None,
                            wall_list=None,nrm=None,
                             pace=1000,  height=1.0, biasfactor=50,
                            temp=300, sigma=0.2, stride=1000, hills_file="HILLS",
                            bias_file="BIAS", label="metad",
                            walker_n=None,walker_id = None,**kwargs):
    """
    Renders a tica plumed dictionary file that can be directly fed in openmm

    :param tica_mdl: project's ticamd
    :param df: data frame
    :param grid_list: list of min and max vals for grid
    :param interval_list: list of min and max vals for interval
    :param pace: gaussian drop rate
    :param biasfactor: gaussian attenuation rate
    :param temp: simulation temp
    :param sigma: sigma
    :param stride: bias file stride
    :param hills_file: hills file
    :param bias_file: bias file
    :param label: metad label
    :param walker_n : number of walkers per tic
    :param walker: current walkers id
    :return:
    dictionary keyed on tica indices
    """

    return_dict = {}

    # inds = np.arange(tica_mdl.n_features)
    # raw_feats = render_raw_features(df,inds)
    # mean_feats = render_mean_free_features(df,inds,tica_mdl,nrm)

    if grid_list is None:
        grid_list = np.repeat(None,n_tics)
    if interval_list is None:
        interval_list = np.repeat(None, n_tics)
    multiple_tics = kwargs.pop('multiple_tics')
    if type(multiple_tics) == int:
        output = []
        output.append("RESTART\n")
        print("Running Multiple tics per simulation. Going up to tic index %d"%n_tics)
        inds = np.unique(np.nonzero(tica_mdl.components_[:multiple_tics,:])[1])
        raw_feats = render_raw_features(df, inds)
        mean_feats = render_mean_free_features(df, inds, tica_mdl, nrm)
        output.append(raw_feats)
        output.append(mean_feats)
        for i in range(multiple_tics):
            output.append(render_tic(df,tica_mdl,i))

        tic_arg_list = ','.join(["tic%d"%i for i in range(multiple_tics)])
        grid_min = ','.join([str(grid_list[i][0]) for i in range(multiple_tics)])
        grid_max = ','.join([str(grid_list[i][1]) for i in range(multiple_tics)])
        current_grid_list = [grid_min, grid_max]
        print(current_grid_list)
        current_interval_list = None
        print(current_interval_list)
        output.append(render_metad_code(arg=tic_arg_list,
                                        sigma=sigma,
                                        height=height,
                                        hills=hills_file,
                                        biasfactor=biasfactor,
                                        pace=pace,
                                        temp=temp,
                                        interval=current_interval_list,
                                        grid = current_grid_list,
                                        label=label,
                                        walker_n=walker_n,
                                        walker_id=walker_id))
        output.append(render_metad_bias_print(arg=tic_arg_list,
                                             stride=stride,
                                             label=label,
                                             file=bias_file))

        return_dict[0] = str(''.join(output))

        return return_dict

    for i in range(n_tics):
        output=[]
        output.append("RESTART\n")
        inds = np.nonzero(tica_mdl.components_[i,:])[0]
        raw_feats = render_raw_features(df, inds)
        mean_feats = render_mean_free_features(df, inds, tica_mdl, nrm)
        output.append(raw_feats)
        output.append(mean_feats)
        output.append(render_tic(df,tica_mdl,i))
        if wall_list is not None:
            output.append(render_tic_wall(arg="tic%d"%i,
                                          wall_limts=wall_list[i],
                                          **kwargs))
        if type(height) == list:
            current_height = height[i]
        else:
            current_height = height
        if type(sigma) == list:
            current_sigma = sigma[i]
        else:
            current_sigma = sigma
        output.append(render_metad_code(arg="tic%d"%i,
                                        sigma=current_sigma,
                                        height=current_height,
                                        hills=hills_file,
                                        biasfactor=biasfactor,
                                        pace=pace,
                                        temp=temp,
                                        interval=interval_list[i],
                                        grid = grid_list[i],
                                        label=label,
                                        walker_n=walker_n,
                                        walker_id=walker_id))
        output.append(render_metad_bias_print(arg="tic%d"%i,
                                             stride=stride,
                                             label=label,
                                             file=bias_file))
        return_dict[i] = str(''.join(output))
    return return_dict


def get_plumed_dict(metad_sim):
    if  type(metad_sim)==str:
        metad_sim = load(metad_sim)
    if not hasattr(metad_sim,"nrm"):
        metad_sim.nrm = None
    if not hasattr(metad_sim,"walker_id"):
        metad_sim.walker_id = None
        metad_sim.walker_n = None
    if not hasattr(metad_sim, "multiple_tics"):
        metad_sim.multiple_tics = None
    return render_tica_plumed_file(tica_mdl=metad_sim.tica_mdl,
                                   df = metad_sim.data_frame,
                                   n_tics=metad_sim.n_tics,
                                   grid=metad_sim.grid,
                                   interval=metad_sim.interval,
                                    wall_list=metad_sim.wall_list,
                                   grid_list=metad_sim.grid_list,
                                   interval_list=metad_sim.interval_list,
                                    pace=metad_sim.pace,
                                   height=metad_sim.height, biasfactor=metad_sim.biasfactor,
                                    temp=metad_sim.temp, sigma=metad_sim.sigma,
                                   stride=metad_sim.stride, hills_file=metad_sim.hills_file,
                                   bias_file=metad_sim.bias_file, label=metad_sim.label,
                                   nrm = metad_sim.nrm, walker_id = metad_sim.walker_id,
                                   walker_n=metad_sim.walker_n,
                                   multiple_tics=metad_sim.multiple_tics)
