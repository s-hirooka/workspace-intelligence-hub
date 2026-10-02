<?php
function sample_register_property_route() {
    register_rest_route('sample/v1', '/properties', ['methods' => 'POST']);
}
