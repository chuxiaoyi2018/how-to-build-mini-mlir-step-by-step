from mlir.ir import *


class Tops:
    WeightOp = 'top.Weight'
    InputOp = 'top.Input'
    AddOp = 'top.Add'
    ReluOp = 'top.Relu'


def checkType(obj, type):
    if not isinstance(obj, type):
        raise AttributeError('{} is not {}'.format(obj, type))


class MLIRImporter(object):
    def __init__(self,
                 input_shapes: list,
                 output_shapes: list,
                 weight_file: str,
                 input_types: list = [],
                 output_types: list = []):
        """
            input_shape: List[List], put module input shape. ex: [[1, 3, 224, 224]]
            output_shape: List, put module output shape. ex: [1, 1000]
        """
        assert (len(weight_file) > 0)
        self.weight_file = weight_file
        self.ctx = Context()
        self.ctx.allow_unregistered_dialects = True
        self.loc = Location.unknown(self.ctx)
        self.ctx.__enter__()
        self.loc.__enter__()
        self.input_shapes = input_shapes
        self.output_shapes = output_shapes
        self.num_input = len(self.input_shapes)
        self.num_output = len(self.output_shapes)
        self.load_weight = dict()
        self.mlir_type = {
            "INT8": IntegerType.get_signless(8),
            "UINT8": IntegerType.get_unsigned(8),
            "SINT8": IntegerType.get_signed(8),
            "INT16": IntegerType.get_signless(16),
            "INT32": IntegerType.get_signless(32),
            "INT64": IntegerType.get_signless(64),
            "F64": F64Type.get(),
            "F32": F32Type.get(),
            "F16": F16Type.get(),
            "BF16": BF16Type.get()
        }
        self.declare_func(input_types, output_types)

    def __del__(self):
        self.loc.__exit__(None, None, None)
        self.ctx.__exit__(None, None, None)

    def ArrayAttr(self, data: list, data_type: str = 'INT64'):
        assert (data_type in self.mlir_type)
        if data_type.find("INT") >= 0:
            return ArrayAttr.get([IntegerAttr.get(self.mlir_type[data_type], x) for x in data])
        if data_type == 'F32':
            return ArrayAttr.get([FloatAttr.get_f32(x) for x in data])
        if data_type == 'F64':
            return ArrayAttr.get([FloatAttr.get_f64(x) for x in data])
        raise RuntimeError("unsupport data type:{}".format(data_type))

    def get_value_type(self, value):
        _type = str(value.type)
        _type = _type.split('<')[-1].split('x')[-1].split('>')[0]
        if _type == "f32":
            return self.mlir_type['F32']
        elif _type == "i8":
            return self.mlir_type['INT8']
        elif _type == "ui8":
            return self.mlir_type['UINT8']
        else:
            raise RuntimeError("No support {}".format(_type))

    def buildOp(self, op_type, operands, output_types, **kargs):
        """
            op_type: String
            inputOpreands: List[pybind.op]
            output_types: List[pybind.op]
            kargs: Dict
        """
        op = Operation.create(op_type, results=output_types, operands=operands, attributes=kargs)
        self.insert_point.insert(op)
        return op.results[0]

    def create_input_op(self, name, index):
        assert (index < len(self.func_args))
        param = {
            "name": StringAttr.get(name),
        }
        op = Operation.create(Tops.InputOp,
                              results=[self.input_types[index]],
                              operands=[self.func_args[index]],
                              attributes=param)
        self.insert_point.insert(op)
        return op.results[0]

    def create_weight_op(self, name, output_shape, data_type="F32"):
        if name in self.load_weight:
            _op, _shape, _type = self.load_weight[name]
            if _shape != output_shape or _type != data_type:
                raise RuntimeError("{} weight conflict".format(name))
            return _op
        tensor_type = RankedTensorType.get(output_shape, self.mlir_type[data_type])
        attributes = {"name": StringAttr.get(name)}
        op = Operation.create(Tops.WeightOp, results=[tensor_type], attributes=attributes)
        self.insert_point.insert(op)
        result = op.results[0]
        self.load_weight[name] = (result, output_shape, data_type)
        return result

    def create_add_op(self, operands, output_shape, **kargs):
        if len(operands) < 2:
            raise RuntimeError("input operand must great than 2")
        output_type = RankedTensorType.get(tuple(output_shape), self.get_value_type(operands[0]))
        param = {'name': StringAttr.get(kargs['name']), 'do_relu': BoolAttr.get(False)}
        if "coeff" in kargs:
            param['coeff'] = self.ArrayAttr(kargs['coeff'], self.mlir_type['F64'])
        return self.buildOp(Tops.AddOp, operands, [output_type], **param)

    def create_relu_op(self, operands, output_shape, **kargs):
        output_type = RankedTensorType.get(tuple(output_shape), self.get_value_type(operands[0]))
        relu_name = StringAttr.get(kargs['name'])
        return self.buildOp(Tops.ReluOp, operands, [output_type], name=relu_name)

    def create_return_op(self, Operands):
        return_op = Operation.create("top.return", operands=Operands, results=[])
        self.insert_point.insert(return_op)
        return return_op

    def print_module(self):
        mlir_format = str(self.mlir_module)
        return mlir_format

    def declare_func(self, input_types: list, output_types: list):
        if len(input_types) == 0:
            input_types = self.num_input * ['F32']
        if len(output_types) == 0:
            output_types = self.num_output * ['F32']

        self.input_types = list()
        self.output_types = list()
        for _shape, _type in zip(self.input_shapes, input_types):
            self.input_types.append(RankedTensorType.get(_shape, self.mlir_type[_type]))
        for _shape, _type in zip(self.output_shapes, output_types):
            self.output_types.append(RankedTensorType.get(_shape, self.mlir_type[_type]))
        args_txt = str()
        for _idx, _type in enumerate(self.input_types):
            args_txt += "%args{}: {}".format(_idx, _type.__str__())
            if (_idx + 1) < self.num_input:
                args_txt += ", "

        output_txt = str()
        for _idx, _type in enumerate(self.output_types):
            output_txt += _type.__str__()
            if (_idx + 1) < self.num_output:
                args_txt += ", "
        if self.num_output > 1:
            output_txt = "({})".format(output_txt)

        mini_func = """
            module attributes {{top.weight_file= \"{weight_file}\"}} {{
                func.func @main({args}) -> {output} {{
                    %0 = \"top.None\"() : () -> none
            }}}}
        """.format(weight_file=self.weight_file, args=args_txt, output=output_txt)
        self.mlir_module = Module.parse(mini_func, self.ctx)
        self.func = self.mlir_module.body.operations[0]
        self.entry_block = self.func.regions[0].blocks[0]
        self.insert_point = InsertionPoint(self.entry_block)
        self.none_op = self.entry_block.operations[0].operation.results[0]

        self.func_args = list()
        for i in self.entry_block.arguments:
            self.func_args.append(i)
