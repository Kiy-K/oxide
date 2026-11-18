<?php

namespace App;

const STORAGE_ROOT = '/var/data';

abstract class Base
{
    protected array $data = [];

    abstract public function find(string $key): string;
}
