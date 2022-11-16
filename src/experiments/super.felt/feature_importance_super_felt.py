import yaml
import torch
from pathlib import Path
import numpy as np
import sys
from captum.attr import KernelShap

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from utils.input_arguments import get_cmd_arguments
from utils import multi_omics_data
from utils.interpretability import (
    compute_importances_values_multiple_inputs,
    save_importance_results,
)
from train_super_felt import train_final
from utils.visualisation import visualize_importances
from utils.choose_gpu import create_device
from models.super_felt_model import SuperFelt

file_directory = Path(__file__).parent
with open((file_directory / "../../config/hyperparameter.yaml"), "r") as stream:
    parameter = yaml.safe_load(stream)

best_hyperparameter = {
    "Cetuximab": {
        "encoder_dropout": 0.3,
        "classifier_dropout": 0.3,
        "classifier_weight_decay": 0.01,
        "encoder_weight_decay": 0.05,
        "learning_rate_e": 0.01,
        "learning_rate_m": 0.01,
        "learning_rate_c": 0.01,
        "learning_rate_classifier": 0.001,
        "e_epochs": 4,
        "m_epochs": 2,
        "c_epochs": 13,
        "classifier_epochs": 14,
        "mini_batch": 32,
        "margin": 1.0,
        "e_dimension": 256,
        "m_dimension": 32,
        "c_dimension": 64,
    },
    "Docetaxel": {
        "encoder_dropout": 0.7,
        "classifier_dropout": 0.5,
        "classifier_weight_decay": 0.1,
        "encoder_weight_decay": 0.0001,
        "learning_rate_e": 0.001,
        "learning_rate_m": 0.001,
        "learning_rate_c": 0.001,
        "learning_rate_classifier": 0.001,
        "e_epochs": 19,
        "m_epochs": 13,
        "c_epochs": 7,
        "classifier_epochs": 15,
        "mini_batch": 8,
        "margin": 0.2,
        "e_dimension": 1024,
        "m_dimension": 256,
        "c_dimension": 512,
    },
}

torch.manual_seed(parameter["random_seed"])
np.random.seed(parameter["random_seed"])


def stacking_feature_importance(
    experiment_name, drug_name, extern_dataset_name, convert_ids, gpu_number
):
    hyperparameter = best_hyperparameter[drug_name]
    device, _ = create_device(gpu_number)
    result_path = Path(
        file_directory,
        "..",
        "..",
        "..",
        "results",
        "stacking",
        "explanation",
        experiment_name,
        drug_name,
    )
    result_path.mkdir(exist_ok=True, parents=True)
    data_path = Path(file_directory, "..", "..", "..", "data")
    (
        gdsc_e,
        gdsc_m,
        gdsc_c,
        gdsc_r,
        extern_e,
        extern_m,
        extern_c,
        extern_r,
    ) = multi_omics_data.load_drug_data_with_elbow(
        data_path, drug_name, extern_dataset_name, return_data_frames=True
    )
    # get columns names
    expression_columns = gdsc_e.columns
    expression_columns = [
        f"Expression {expression_gene}" for expression_gene in expression_columns
    ]

    mutation_columns = gdsc_m.columns
    mutation_columns = [
        f"Mutation {mutation_gene}" for mutation_gene in mutation_columns
    ]

    cna_columns = gdsc_c.columns
    cna_columns = [f"CNA {cna_gene}" for cna_gene in cna_columns]

    all_columns = np.concatenate([expression_columns, mutation_columns, cna_columns])

    gdsc_e = gdsc_e.to_numpy()
    gdsc_m = gdsc_m.to_numpy()
    gdsc_c = gdsc_c.to_numpy()
    extern_e = extern_e.to_numpy()
    extern_m = torch.FloatTensor(extern_m.to_numpy()).to(device)
    extern_c = torch.FloatTensor(extern_c.to_numpy()).to(device)

    number_of_expression_features = gdsc_e.shape[1]
    number_of_mutation_features = gdsc_m.shape[1]

    e_encoder, m_encoder, c_encoder, classifier, scaler_gdsc = train_final(
        gdsc_e,
        gdsc_m,
        gdsc_c,
        gdsc_r,
        hyperparameter,
        device,
        False,
    )
    classifier.eval()

    gdsc_e_scaled = torch.Tensor(scaler_gdsc.fit_transform(gdsc_e))
    gdsc_e_scaled = gdsc_e_scaled.to(device)
    gdsc_m = torch.FloatTensor(gdsc_m).to(device)
    gdsc_c = torch.FloatTensor(gdsc_c).to(device)

    extern_e_scaled = torch.Tensor(scaler_gdsc.transform(extern_e)).to(device)
    responder_indices = np.random.choice(
        np.where(gdsc_r == 1)[0], size=5, replace=False
    )
    non_responder_indices = np.random.choice(
        np.where(gdsc_r == 0)[0], size=5, replace=False
    )
    all_indices = np.concatenate([responder_indices, non_responder_indices])
    scaled_baseline = (
        gdsc_e_scaled[all_indices],
        gdsc_m[all_indices],
        gdsc_c[all_indices],
    )

    super_felt_model = SuperFelt(e_encoder, m_encoder, c_encoder, classifier)
    gdsc_e_scaled.requires_grad_()
    gdsc_m.requires_grad_()
    gdsc_c.requires_grad_()
    integradet_gradients = KernelShap(super_felt_model)

    all_attributions_test = compute_importances_values_multiple_inputs(
        (gdsc_e_scaled, gdsc_m, gdsc_c),
        integradet_gradients,
        scaled_baseline,
    )

    visualize_importances(
        all_columns,
        all_attributions_test,
        path=result_path,
        file_name="all_attributions_test",
        convert_ids=convert_ids,
        number_of_expression_features=number_of_expression_features,
        number_of_mutation_features=number_of_mutation_features,
    )

    extern_e_scaled.requires_grad_()
    extern_m.requires_grad_()
    extern_c.requires_grad_()
    all_attributions_extern = compute_importances_values_multiple_inputs(
        (extern_e_scaled, extern_m, extern_c), integradet_gradients, scaled_baseline
    )

    visualize_importances(
        all_columns,
        all_attributions_extern,
        path=result_path,
        file_name="all_attributions_extern",
        convert_ids=convert_ids,
        number_of_expression_features=number_of_expression_features,
        number_of_mutation_features=number_of_mutation_features,
    )

    save_importance_results(
        all_attributions_test,
        all_columns,
        result_path,
        "extern",
    )
    save_importance_results(
        all_attributions_extern,
        all_columns,
        result_path,
        "test",
    )


if __name__ == "__main__":
    args = get_cmd_arguments()

    if args.drug == "all":
        for drug, extern_dataset in parameter["drugs"].items():
            stacking_feature_importance(
                args.experiment_name,
                drug,
                extern_dataset,
                args.convert_ids,
                args.gpu_number,
            )
    else:
        extern_dataset = parameter["drugs"][args.drug]
        stacking_feature_importance(
            args.experiment_name,
            args.drug,
            extern_dataset,
            args.convert_ids,
            args.gpu_number,
        )
