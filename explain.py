import os
import random
from enum import Enum
from pathlib import Path

import networkx as nx
import numpy as np
import torch
import typer
from tqdm import tqdm as tq

from models import GcnEncoderGraph

app = typer.Typer()


class ExplainMethod(str, Enum):
    contrastive = 'contrastive'
    sa = 'sensitivity'
    occlusion = 'occlusion'
    random = 'random'


def load_model(model_path: Path):
    ckpt = torch.load(model_path)
    cg_dict = ckpt["cg"]  # get computation graph
    input_dim = cg_dict["feat"].shape[2]
    num_classes = cg_dict["pred"].shape[2]
    model = GcnEncoderGraph(
        input_dim=input_dim,
        hidden_dim=20,
        embedding_dim=20,
        label_dim=num_classes,
        num_layers=3,
        bn=False,
        args=None,
    )
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model


def check_path(output_path: Path):
    if not output_path.exists():
        typer.confirm("Output path does not exist, do you want to create it?", abort=True)
        output_path.mkdir(parents=True)


def read_graphs(dataset_path: Path):
    labels = {}
    nx_graphs = {}
    for name in os.listdir(str(dataset_path)):
        idx, label = name.split('.')[-3:-1]
        nx_graphs[idx] = nx.read_gexf(dataset_path / name)
        labels[idx] = int(label)
    print('Found %d samples' % len(nx_graphs))
    return nx_graphs, labels


@app.command(name='sensitivity', help='Run sensitivity analysis explanation')
def sa(dataset_path: Path, model_path: Path, output_path: Path):
    check_path(output_path)
    nx_graphs, labels = read_graphs(dataset_path)
    model = load_model(model_path)

    def explain(graph_num):
        g = nx_graphs[graph_num]
        node_count = len(g.nodes)

        adj = np.zeros((1, 100, 100))
        adj[0, :node_count, :node_count] = nx.to_numpy_matrix(g)
        adj = torch.tensor(adj, dtype=torch.float)
        x = torch.ones((1, 100, 10), requires_grad=True, dtype=torch.float)

        ypred, _ = model(x, adj)

        loss = model.loss(ypred, torch.LongTensor([labels[graph_num]]))
        loss.backward()
        node_importance = x.grad.detach().numpy()[0][:node_count]
        node_importance = (node_importance ** 2).sum(axis=1)
        N = nx_graphs[graph_num].number_of_nodes()
        masked_adj = np.zeros((N, N))
        for u, v in nx_graphs[graph_num].edges():
            u = int(u)
            v = int(v)
            masked_adj[u, v] = masked_adj[v, u] = node_importance[u] + node_importance[v]
        return masked_adj

    for gid in tq(nx_graphs):
        masked_adj = explain(gid)
        np.save(output_path / ('%s.npy' % gid), masked_adj)


@app.command(help='Run occlusion explanation')
def occlusion(dataset_path: Path, model_path: Path, output_path: Path):
    check_path(output_path)
    nx_graphs, labels = read_graphs(dataset_path)
    model = load_model(model_path)

    def prepare_input(g):
        node_count = len(g.nodes)
        adj = np.zeros((1, 100, 100))
        adj[0, :node_count, :node_count] = nx.to_numpy_matrix(g)
        adj = torch.tensor(adj, dtype=torch.float)
        x = torch.ones((1, 100, 10), requires_grad=False, dtype=torch.float)
        return x, adj

    def explain(graph_num):
        model.eval()
        g = nx_graphs[graph_num]
        x, adj = prepare_input(g)

        ypred, _ = model(x, adj)
        true_label = labels[graph_num]
        before_occlusion = ypred[0].softmax(0)
        node_importance = {}

        for removed_node in g.nodes():
            g2 = g.copy()
            g2.remove_node(removed_node)
            x, adj = prepare_input(g2)
            ypred, _ = model(x, adj)
            after_occlusion = ypred[0].softmax(0)
            importance = abs(after_occlusion[true_label] - before_occlusion[true_label])
            node_importance[int(removed_node)] = importance.item()

        N = nx_graphs[graph_num].number_of_nodes()
        masked_adj = np.zeros((N, N))
        for u, v in nx_graphs[graph_num].edges():
            u = int(u)
            v = int(v)
            masked_adj[u, v] = masked_adj[v, u] = node_importance[u] + node_importance[v]
        return masked_adj

    for gid in tq(nx_graphs):
        masked_adj = explain(gid)
        np.save(output_path / ('%s.npy' % gid), masked_adj)


@app.command(name='random', help='Run random explanation')
def random_explain(dataset_path: Path, output_path: Path):
    check_path(output_path)
    nx_graphs, labels = read_graphs(dataset_path)

    def explain(graph_num):
        g = nx_graphs[graph_num]
        random_importance = list(range(len(g.edges())))
        random.shuffle(random_importance)

        N = g.number_of_nodes()
        masked_adj = np.zeros((N, N))
        for (u, v), importance in zip(g.edges(), random_importance):
            u = int(u)
            v = int(v)
            masked_adj[u, v] = masked_adj[v, u] = importance
        return masked_adj

    for gid in tq(nx_graphs):
        masked_adj = explain(gid)
        np.save(output_path / ('%s.npy' % gid), masked_adj)


if __name__ == "__main__":
    app()
