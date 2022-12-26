from transformers.modeling_outputs import BaseModelOutputWithPoolingAndCrossAttentions, TokenClassifierOutput
from src.lambert.embedding import EmbeddingWrapper

from torch import nn
import torch

from src.lambert.relative import RelativeBias


class LambertModel(nn.Module):
    """
    A wrapper over `RobertaModel`, providing patched `forward` method accepting an additional `bboxes` argument

    Args:
        bert (RobertaModel): original RobertaModel instance
        base (int): `base` parameter of `LayoutEmbeddings`
    """

    def __init__(self, bert, base: int = 500):
        super().__init__()
        self.bert = bert

        # add attributes to avoid modifications in code copied from `transformers`
        self.config = bert.config
        self.embeddings = EmbeddingWrapper(bert.embeddings, base)
        self.encoder = bert.encoder
        self.pooler = bert.pooler
        self.get_extended_attention_mask = bert.get_extended_attention_mask
        self.invert_attention_mask = bert.invert_attention_mask
        self.get_head_mask = bert.get_head_mask

        # add relative bias
        self.relative_bias = RelativeBias(self.encoder.config.num_attention_heads)

    # copied from transformers.models.bert.modeling_bert.RobertaModel,
    # modifications indicated in comments
    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        encoder_hidden_states=None,
        encoder_attention_mask=None,
        past_key_values=None,
        use_cache=None,
        output_attentions=None,
        output_hidden_states=None,
        return_dict=None,
        bboxes=None  # added bboxes argument
    ) -> BaseModelOutputWithPoolingAndCrossAttentions:
        """
        `bboxes` is a tensor of shape (batch_size, sequence_length, 4); see `RobertaModel` for the remaining args
        """
        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        )
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict
        use_cache = use_cache if use_cache is not None else self.config.use_cache

        if not self.config.is_decoder:
            use_cache = False

        if input_ids is not None and inputs_embeds is not None:
            raise ValueError("You cannot specify both input_ids and inputs_embeds at the same time")
        elif input_ids is not None:
            input_shape = input_ids.size()
            batch_size, seq_length = input_shape
        elif inputs_embeds is not None:
            input_shape = inputs_embeds.size()[:-1]
            batch_size, seq_length = input_shape
        else:
            raise ValueError("You have to specify either input_ids or inputs_embeds")

        device = input_ids.device if input_ids is not None else inputs_embeds.device

        past_key_values_length = past_key_values[0][0].shape[2] if past_key_values is not None else 0

        if attention_mask is None:
            attention_mask = torch.ones(((batch_size, seq_length + past_key_values_length)), device=device)
        if token_type_ids is None:
            token_type_ids = torch.zeros(input_shape, dtype=torch.long, device=device)

        extended_attention_mask: torch.Tensor = self.get_extended_attention_mask(attention_mask, input_shape)

        if self.config.is_decoder and encoder_hidden_states is not None:
            encoder_batch_size, encoder_sequence_length, _ = encoder_hidden_states.size()
            encoder_hidden_shape = (encoder_batch_size, encoder_sequence_length)
            if encoder_attention_mask is None:
                encoder_attention_mask = torch.ones(encoder_hidden_shape, device=device)
            encoder_extended_attention_mask = self.invert_attention_mask(encoder_attention_mask)
        else:
            encoder_extended_attention_mask = None

        head_mask = self.get_head_mask(head_mask, self.config.num_hidden_layers)

        embedding_output = self.embeddings(
            input_ids=input_ids,
            position_ids=position_ids,
            token_type_ids=token_type_ids,
            inputs_embeds=inputs_embeds,
            past_key_values_length=past_key_values_length,
            bboxes=bboxes  # added bboxes argument
        )

        # extended attention mask is applied additively, so we can simply inject it with the relative biases
        bias = self.relative_bias(input_ids, bboxes)
        extended_attention_mask = extended_attention_mask + bias

        encoder_outputs = self.encoder(
            embedding_output,
            attention_mask=extended_attention_mask,
            head_mask=head_mask,
            encoder_hidden_states=encoder_hidden_states,
            encoder_attention_mask=encoder_extended_attention_mask,
            past_key_values=past_key_values,
            use_cache=use_cache,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
        )
        sequence_output = encoder_outputs[0]
        pooled_output = self.pooler(sequence_output) if self.pooler is not None else None

        if not return_dict:
            return (sequence_output, pooled_output) + encoder_outputs[1:]

        return BaseModelOutputWithPoolingAndCrossAttentions(
            last_hidden_state=sequence_output,
            pooler_output=pooled_output,
            past_key_values=encoder_outputs.past_key_values,
            hidden_states=encoder_outputs.hidden_states,
            attentions=encoder_outputs.attentions,
            cross_attentions=encoder_outputs.cross_attentions,
        )
