import os
import tensorflow as tf
import argparse
import json
from tensorflow.python.keras.callbacks import Callback
from minio import Minio
from minio.error import ResponseError




class MyModel(object):
    def train(self):
        mnist = tf.keras.datasets.mnist
        strategy = tf.distribute.MirroredStrategy()
        print('장치의 수: {}'.format(strategy.num_replicas_in_sync))

        # 입력 값을 받게 추가합니다.
        parser = argparse.ArgumentParser()
        parser.add_argument('--learning_rate', required=False, type=float, default=0.01)
        parser.add_argument('--dropout_rate', required=False, type=float, default=0.2)
        parser.add_argument('--checkpoint_dir', required=False, default='/reuslt/training_checkpoints')
        parser.add_argument('--model_version', required=False, default='001')
        parser.add_argument('--saved_model_dir', required=False, default='/result/saved_model')        
        parser.add_argument('--tensorboard_log', required=False, default='/result/log')                
        args = parser.parse_args()

        tensorboard_log = args.tensorboard_log + "/" + args.model_version
        
        (x_train, y_train), (x_test, y_test) = mnist.load_data()
        x_train, x_test = x_train / 255.0, x_test / 255.0
        
        with strategy.scope():
            model = tf.keras.models.Sequential([
                tf.keras.layers.Flatten(input_shape=(28, 28)),
                tf.keras.layers.Dense(128, activation='relu'),
                tf.keras.layers.Dropout(args.dropout_rate),
                tf.keras.layers.Dense(10, activation='softmax')
            ])

            sgd = tf.keras.optimizers.SGD(lr=args.learning_rate,
                                          decay=1e-6,
                                          momentum=0.9,
                                          nesterov=True)

            model.compile(optimizer=sgd,
                          loss='sparse_categorical_crossentropy',
                          metrics=['acc'])

            # 체크포인트를 저장할 체크포인트 디렉터리를 지정합니다.
            checkpoint_dir = args.checkpoint_dir
            # 체크포인트 파일의 이름
            checkpoint_prefix = os.path.join(checkpoint_dir, "ckpt_{epoch}")            

            model.fit(x_train, y_train,
                      verbose=0,
                      validation_data=(x_test, y_test),
                      epochs=5,
                      callbacks=[KatibMetricLog(),
                                tf.keras.callbacks.TensorBoard(log_dir=tensorboard_log),
                                tf.keras.callbacks.ModelCheckpoint(filepath=checkpoint_prefix,
                                       save_weights_only=True)
                                ])
            minioClient = Minio('minio-service.kubeflow.svc.cluster.local:9000',
                  access_key='minio',
                  secret_key='minio123',
                  secure=False)            
            
            path = args.saved_model_dir + "/" + args.model_version        
            model.save(path, save_format='tf')
            
            for currentpath, folders, files in os.walk(tensorboard_log):
                for file in files: 
                    print(os.path.join(currentpath, file))
                    log_file = str(os.path.join(currentpath, file))
                    minioClient.fput_object('tensorboard', log_file[1:], log_file)
                    
            # for Tensorboard artifact minio:// s3:// :<
            metadata = {
                'outputs': [{
                    'type': 'tensorboard',
                    'source': 's3://tensorboard' + tensorboard_log
                }]
            }
            
            with open('/mlpipeline-ui-metadata.json', 'w') as f:
              json.dump(metadata, f)            

class KatibMetricLog(Callback):
    def on_batch_end(self, batch, logs={}):
        print("batch", str(batch),
              "accuracy=" + str(logs.get('acc')),
              "loss=" + str(logs.get('loss')))

    def on_epoch_begin(self, epoch, logs={}):
        print("epoch " + str(epoch) + ":")

    def on_epoch_end(self, epoch, logs={}):
        print("Validation-accuracy=" + str(logs.get('val_acc')),
              "Validation-loss=" + str(logs.get('val_loss')))
        return


if __name__ == '__main__':
    if os.getenv('FAIRING_RUNTIME', None) is None:
        from kubeflow import fairing
        from kubeflow.fairing.kubernetes import utils as k8s_utils

        DOCKER_REGISTRY = 'kubeflow-registry.default.svc.cluster.local:30000'
        fairing.config.set_builder(
            'append',
            image_name='katib-job',
            base_image='brightfly/kubeflow-jupyter-lab:tf2.0-gpu',
            registry=DOCKER_REGISTRY,
            push=True)
        # cpu 1, memory 1GiB
        fairing.config.set_deployer('job',
                                    namespace='handson5'
                                    )
        # python3
        import IPython
        ipy = IPython.get_ipython()
        if ipy is None:
            fairing.config.set_preprocessor('python', input_files=[__file__])        
        fairing.config.run()
    else:
        remote_train = MyModel()
        remote_train.train()
